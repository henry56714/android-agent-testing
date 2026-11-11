package org.example.aat.sdk;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

public final class SerialWorker implements AutoCloseable {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    public Future<?> submit(Runnable task) { return executor.submit(task); }
    @Override public void close() throws InterruptedException {
        executor.shutdownNow();
        if (!executor.awaitTermination(5, TimeUnit.SECONDS)) {
            throw new IllegalStateException("Worker did not terminate");
        }
    }
}
