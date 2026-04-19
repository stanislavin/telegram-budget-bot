package com.expensetracker.notif.ui

import android.content.Intent
import android.provider.Settings
import android.text.TextUtils
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.expensetracker.notif.data.NotificationEntity
import com.expensetracker.notif.service.NotificationCaptureService
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NotificationsScreen(
    viewModel: NotificationsViewModel = viewModel(),
    isListenerEnabled: () -> Boolean
) {
    val items by viewModel.notifications.collectAsState()
    val context = LocalContext.current
    val enabled = isListenerEnabled()

    Scaffold(
        topBar = { TopAppBar(title = { Text("Expense Tracker") }) }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            if (!enabled) {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(24.dp),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Text(
                        "Grant notification access",
                        style = androidx.compose.material3.MaterialTheme.typography.titleLarge
                    )
                    Text(
                        "To capture notifications from other apps, enable notification access for Expense Tracker in system settings.",
                        modifier = Modifier.padding(vertical = 16.dp)
                    )
                    Button(onClick = {
                        context.startActivity(
                            Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        )
                    }) { Text("Open settings") }
                }
            } else if (items.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("No notifications captured yet.")
                }
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    items(items, key = { it.id }) { n ->
                        NotificationRow(n)
                        HorizontalDivider()
                    }
                }
            }
        }
    }
}

private val timeFormatter = SimpleDateFormat("MMM d, HH:mm:ss", Locale.getDefault())

@Composable
private fun NotificationRow(n: NotificationEntity) {
    Column(Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
        Text(
            text = n.appLabel ?: n.packageName,
            fontWeight = FontWeight.SemiBold
        )
        if (!n.title.isNullOrBlank()) {
            Text(n.title, fontWeight = FontWeight.Medium)
        }
        if (!n.text.isNullOrBlank()) {
            Text(n.text)
        }
        Text(
            timeFormatter.format(Date(n.postedAt)),
            style = androidx.compose.material3.MaterialTheme.typography.labelSmall
        )
    }
}

fun isNotificationListenerEnabled(context: android.content.Context): Boolean {
    val flat = Settings.Secure.getString(
        context.contentResolver,
        "enabled_notification_listeners"
    ) ?: return false
    val expected = android.content.ComponentName(
        context,
        NotificationCaptureService::class.java
    ).flattenToString()
    val splitter = TextUtils.SimpleStringSplitter(':')
    splitter.setString(flat)
    for (name in splitter) {
        if (name == expected) return true
    }
    return false
}
