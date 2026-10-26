package com.resilienceops.app;

/** Entry point: reads configuration from the environment and starts the HTTP service. */
public final class App {

    private App() {
    }

    public static void main(String[] args) throws Exception {
        Config config = Config.fromEnv();
        ResilienceServer server = new ResilienceServer(config);
        Runtime.getRuntime().addShutdownHook(new Thread(server::stop, "shutdown"));
        server.start();
    }
}
