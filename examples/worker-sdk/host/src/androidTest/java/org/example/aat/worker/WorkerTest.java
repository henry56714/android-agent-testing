package org.example.aat.worker;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.example.aat.sdk.SerialWorker;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.assertEquals;

@RunWith(AndroidJUnit4.class)
public class WorkerTest {
    @Test public void sdkRunsInHost() throws Exception {
        AtomicInteger value = new AtomicInteger();
        try (SerialWorker worker = new SerialWorker()) {
            worker.submit(() -> value.incrementAndGet()).get(5, TimeUnit.SECONDS);
            assertEquals(1, value.get());
        }
    }
}
