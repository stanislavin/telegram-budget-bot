package com.expensetracker.notif.service

import android.app.Notification
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.expensetracker.notif.data.AppDatabase
import com.expensetracker.notif.data.AppFilterPrefs
import com.expensetracker.notif.data.NotificationEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class NotificationCaptureService : NotificationListenerService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val extras = sbn.notification?.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()

        if (title.isNullOrBlank() && text.isNullOrBlank()) return

        val packageName = sbn.packageName
        if (!AppFilterPrefs.isAllowed(applicationContext, packageName)) return

        val appLabel = runCatching {
            val pm = packageManager
            val info = pm.getApplicationInfo(packageName, 0)
            pm.getApplicationLabel(info).toString()
        }.getOrNull()

        val entity = NotificationEntity(
            packageName = packageName,
            appLabel = appLabel,
            title = title,
            text = text,
            postedAt = sbn.postTime
        )

        scope.launch {
            AppDatabase.get(applicationContext).notifications().insert(entity)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
    }
}
