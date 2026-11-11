package org.example.aat.counter;

import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class CounterTest {
    @Test public void incrementsExactlyOnce() {
        Counter c = new Counter();
        assertEquals(0, c.value());
        c.increment();
        assertEquals(1, c.value());
    }
}
