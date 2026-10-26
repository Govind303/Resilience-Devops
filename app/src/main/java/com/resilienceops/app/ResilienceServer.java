package com.resilienceops.app;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The system under test. It serves quotes fetched from a downstream service.
 * In "resilient" mode it degrades gracefully when the dependency is unreachable (cached quote,
 * /health reports DEGRADED); in "fragile" mode it fails hard, which the chaos gate should catch.
 */
public final class ResilienceServer {

    static final String DEFAULT_QUOTE = "Everything fails, all the time.";

    private final Config config;
    private final HttpServer server;
    private final HttpClient client;
    private final ExecutorService workers = Executors.newFixedThreadPool(16);
    private final ScheduledExecutorService checker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "downstream-checker");
        t.setDaemon(true);
        return t;
    });
    private final AtomicBoolean downstreamUp = new AtomicBoolean(true);
    private final AtomicReference<String> lastQuote = new AtomicReference<>(DEFAULT_QUOTE);
    private final Instant startedAt = Instant.now();
    /** Changes on every process start, so the chaos gate can prove a killed process was replaced. */
    private final String instanceId = UUID.randomUUID().toString().substring(0, 8);

    public ResilienceServer(Config config) throws IOException {
        this.config = config;
        this.client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofMillis(config.downstreamTimeoutMs()))
                .build();
        this.server = HttpServer.create(new InetSocketAddress(config.port()), 0);
        server.createContext("/health", this::health);
        server.createContext("/api/quote", this::quote);
        server.createContext("/", this::root);
        server.setExecutor(workers);
    }

    public void start() {
        server.start();
        checker.scheduleWithFixedDelay(this::refreshDownstreamStatus, 0, config.checkIntervalMs(), TimeUnit.MILLISECONDS);
        System.out.printf("ResilienceOps app %s (%s, mode=%s) listening on port %d%n",
                config.version(), config.color(), config.mode(), port());
    }

    public void stop() {
        checker.shutdownNow();
        server.stop(0);
        workers.shutdownNow();
    }

    public int port() {
        return server.getAddress().getPort();
    }

    /** Re-checks the dependency; called on a timer and directly by tests. */
    void refreshDownstreamStatus() {
        downstreamUp.set(fetchQuote().isPresent());
    }

    private Optional<String> fetchQuote() {
        try {
            HttpRequest request = HttpRequest.newBuilder(URI.create(config.downstreamUrl()))
                    .timeout(Duration.ofMillis(config.downstreamTimeoutMs()))
                    .GET()
                    .build();
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                return Optional.empty();
            }
            return Optional.ofNullable(Json.extractString(response.body(), "quote"));
        } catch (IOException e) {
            return Optional.empty();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return Optional.empty();
        }
    }

    private void health(HttpExchange exchange) throws IOException {
        if (!isGet(exchange)) {
            return;
        }
        boolean up = downstreamUp.get();
        String status = up ? "UP" : (config.resilient() ? "DEGRADED" : "DOWN");
        long uptime = Duration.between(startedAt, Instant.now()).toSeconds();
        String body = String.format(
                "{\"status\":\"%s\",\"mode\":\"%s\",\"version\":\"%s\",\"color\":\"%s\",\"downstream\":\"%s\",\"uptimeSeconds\":%d,\"instanceId\":\"%s\"}",
                status, Json.escape(config.mode()), Json.escape(config.version()), Json.escape(config.color()),
                up ? "UP" : "DOWN", uptime, instanceId);
        send(exchange, "DOWN".equals(status) ? 503 : 200, body);
    }

    private void quote(HttpExchange exchange) throws IOException {
        if (!isGet(exchange)) {
            return;
        }
        Optional<String> fresh = fetchQuote();
        downstreamUp.set(fresh.isPresent());
        if (fresh.isPresent()) {
            lastQuote.set(fresh.get());
            send(exchange, 200, quoteJson(fresh.get(), "downstream", false));
        } else if (config.resilient()) {
            send(exchange, 200, quoteJson(lastQuote.get(), "fallback", true));
        } else {
            send(exchange, 502, "{\"error\":\"downstream quote service unavailable\"}");
        }
    }

    private void root(HttpExchange exchange) throws IOException {
        if (!isGet(exchange)) {
            return;
        }
        String body = String.format("{\"service\":\"resilienceops-app\",\"version\":\"%s\",\"endpoints\":[\"/health\",\"/api/quote\"]}",
                Json.escape(config.version()));
        send(exchange, 200, body);
    }

    private static String quoteJson(String quote, String source, boolean degraded) {
        return String.format("{\"quote\":\"%s\",\"source\":\"%s\",\"degraded\":%s}", Json.escape(quote), source, degraded);
    }

    private static boolean isGet(HttpExchange exchange) throws IOException {
        if ("GET".equals(exchange.getRequestMethod())) {
            return true;
        }
        send(exchange, 405, "{\"error\":\"method not allowed\"}");
        return false;
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }
}
