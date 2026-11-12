package org.example.aat.counter;

import org.junit.Test;
import static org.junit.Assert.assertEquals;

/** Added after onboarding: full discovery must execute it without a manual case YAML. */
public class CounterIsolationTest {
    @Test public void instancesDoNotShareState() {
        Counter first = new Counter();
        Counter second = new Counter();
        first.increment();
        first.increment();
        assertEquals(2, first.value());
        assertEquals(0, second.value());
    }
}
