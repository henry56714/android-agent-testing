package org.example.aat.sdk;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import org.junit.Test;
import static org.junit.Assert.*;

public class SerialWorkerTest {
    @Test public void preservesOrderAfterFailure() throws Exception {
        List<Integer> values = new ArrayList<>();
        try (SerialWorker worker = new SerialWorker()) {
            worker.submit(() -> values.add(1)).get(5, TimeUnit.SECONDS);
            try {
                worker.submit(() -> { throw new IllegalStateException("injected"); }).get(5, TimeUnit.SECONDS);
                fail("Expected injected failure");
            } catch (ExecutionException expected) {
                assertTrue(expected.getCause() instanceof IllegalStateException);
            }
            worker.submit(() -> values.add(2)).get(5, TimeUnit.SECONDS);
            assertEquals(java.util.Arrays.asList(1, 2), values);
        }
    }
}
