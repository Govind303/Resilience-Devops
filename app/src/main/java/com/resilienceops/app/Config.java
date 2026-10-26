package com.resilienceops.app;

import java.util.Map;

/**
 * Runtime settings, all taken from environment variables so Ansible can set them per instance.
 *
 * @param port                HTTP port (APP_PORT, default 8080)
 * @param downstreamUrl       quote service the app depends on (DOWNSTREAM_URL)
 * @param downstreamTimeoutMs how long to wait for the dependency before giving up (DOWNSTREAM_TIMEOUT_MS)
 * @param checkIntervalMs     how often the background check refreshes dependency health (CHECK_INTERVAL_MS)
 * @param mode                "resilient" degrades gracefully; "fragile" fails hard (RESILIENCE_MODE)
 * @param version             build version shown in /health (APP_VERSION)
 * @param color               blue/green slot name, or "staging" (APP_COLOR)
 */
public record Config(int port, String downstreamUrl, int downstreamTimeoutMs, long checkIntervalMs,
                     String mode, String version, String color) {

    public static Config fromEnv() {
        return fromMap(System.getenv());
    }

    static Config fromMap(Map<String, String> env) {
        return new Config(
                Integer.parseInt(env.getOrDefault("APP_PORT", "8080")),
                env.getOrDefault("DOWNSTREAM_URL", "http://127.0.0.1:9090/quote"),
                Integer.parseInt(env.getOrDefault("DOWNSTREAM_TIMEOUT_MS", "800")),
                Long.parseLong(env.getOrDefault("CHECK_INTERVAL_MS", "2000")),
                env.getOrDefault("RESILIENCE_MODE", "resilient"),
                env.getOrDefault("APP_VERSION", "dev"),
                env.getOrDefault("APP_COLOR", "local"));
    }

    public boolean resilient() {
        return !"fragile".equalsIgnoreCase(mode);
    }
}
