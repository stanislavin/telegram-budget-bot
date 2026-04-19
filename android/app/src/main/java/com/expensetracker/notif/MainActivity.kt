package com.expensetracker.notif

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.expensetracker.notif.ui.ExpenseTrackerTheme
import com.expensetracker.notif.ui.NotificationsScreen
import com.expensetracker.notif.ui.isNotificationListenerEnabled
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            ExpenseTrackerTheme {
                val enabled = remember { mutableStateOf(isNotificationListenerEnabled(this)) }
                lifecycleScope.launch {
                    repeatOnLifecycle(Lifecycle.State.RESUMED) {
                        enabled.value = isNotificationListenerEnabled(this@MainActivity)
                    }
                }
                NotificationsScreen(isListenerEnabled = { enabled.value })
            }
        }
    }
}
