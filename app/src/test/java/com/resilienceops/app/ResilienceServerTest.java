package com.resilienceops.app;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ResilienceServerTest {

    private final HttpClient http = HttpClient.newHttpClient();
    private HttpServer downstream;
    private ResilienceServer app;

    @BeforeEach
    void startDownstream() throws Exception {
        downstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        downstream.createContext("/quote", exchange -> {
            byte[] body = "{\"quote\":\"Stay resilient\"}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(body);
            }
        });
        downstream.start();
    }

    @AfterEach
    void stopAll() {
        if (app != null) {
            app.stop();
        }
        downstream.stop(0);
    }

    private void startApp(String mode) throws Exception {
        Config config = Config.fromMap(Map.of(
                "APP_PORT", "0",
                "DOWNSTREAM_URL", "http://127.0.0.1:" + downstream.getAddress().getPort() + "/quote",
                "DOWNSTREAM_TIMEOUT_MS", "500",
                "CHECK_INTERVAL_MS", "60000",
                "RESILIENCE_MODE", mode));
        app = new ResilienceServer(config);
        app.start();
    }

    private HttpResponse<String> get(String path) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + app.port() + path)).GET().build();
        return http.send(request, HttpResponse.BodyHandlers.ofString());
    }

    @Test
    void healthIsUpWhenDependencyIsUp() throws Exception {
        startApp("resilient");
        app.refreshDownstreamStatus();
        HttpResponse<String> response = get("/health");
        assertEquals(200, response.statusCode());
        assertTrue(response.body().contains("\"status\":\"UP\""), response.body());
    }

    @Test
    void quoteIsServedFromDownstream() throws Exception {
        startApp("resilient");
        HttpResponse<String> response = get("/api/quote");
        assertEquals(200, response.statusCode());
        assertTrue(response.body().contains("Stay resilient"));
        assertTrue(response.body().contains("\"source\":\"downstream\""));
    }

    @Test
    void resilientModeDegradesGracefullyWhenDependencyFails() throws Exception {
        startApp("resilient");
        get("/api/quote");            // caches a real quote
        downstream.stop(0);

        HttpResponse<String> quote = get("/api/quote");
        assertEquals(200, quote.statusCode());
        assertTrue(quote.body().contains("\"source\":\"fallback\""), quote.body());
        assertTrue(quote.body().contains("\"degraded\":true"));
        assertTrue(quote.body().contains("Stay resilient"), "fallback should reuse the last good quote");

        HttpResponse<String> health = get("/health");
        assertEquals(200, health.statusCode());
        assertTrue(health.body().contains("\"status\":\"DEGRADED\""), health.body());
    }

    @Test
    void fragileModeFailsHardWhenDependencyFails() throws Exception {
        startApp("fragile");
        downstream.stop(0);

        assertEquals(502, get("/api/quote").statusCode());
        HttpResponse<String> health = get("/health");
        assertEquals(503, health.statusCode());
        assertTrue(health.body().contains("\"status\":\"DOWN\""));
    }

    @Test
    void rejectsNonGetRequests() throws Exception {
        startApp("resilient");
        HttpRequest post = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + app.port() + "/health"))
                .POST(HttpRequest.BodyPublishers.noBody()).build();
        assertEquals(405, http.send(post, HttpResponse.BodyHandlers.ofString()).statusCode());
    }

    @Test
    void configDefaultsAreSensible() {
        Config config = Config.fromMap(Map.of());
        assertEquals(8080, config.port());
        assertTrue(config.resilient());
        assertEquals("http://127.0.0.1:9090/quote", config.downstreamUrl());
    }

    @Test
    void jsonRoundTripsEscapedText() {
        String json = "{\"quote\":\"" + Json.escape("say \"hi\"\n") + "\"}";
        assertEquals("say \"hi\"\n", Json.extractString(json, "quote"));
    }
}
