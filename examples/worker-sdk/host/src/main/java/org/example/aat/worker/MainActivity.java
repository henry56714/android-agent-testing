package org.example.aat.worker;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        org.example.aat.sdk.Counter counter = new org.example.aat.sdk.Counter();
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(32, 80, 32, 32);
        TextView value = new TextView(this);
        value.setText("Count: 0");
        value.setTextSize(30);
        Button button = new Button(this);
        button.setText("Increment");
        button.setOnClickListener(v -> {
            counter.increment();
            value.setText("Count: " + counter.value());
        });
        layout.addView(value);
        layout.addView(button);
        setContentView(layout);
    }
}
