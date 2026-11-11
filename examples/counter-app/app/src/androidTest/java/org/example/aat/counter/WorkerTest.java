package org.example.aat.counter;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class WorkerTest {
    @Test public void executesOnWorker() throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger();
        Thread worker = new Thread(() -> { count.incrementAndGet(); done.countDown(); }, "aat-worker");
        try {
            worker.start();
            assertTrue(done.await(5, TimeUnit.SECONDS));
            assertEquals(1, count.get());
        } finally {
            worker.interrupt();
            worker.join(5000);
            assertFalse(worker.isAlive());
        }
    }
}
