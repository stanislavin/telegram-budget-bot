package com.expensetracker.notif.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.expensetracker.notif.data.AppDatabase
import com.expensetracker.notif.data.NotificationEntity
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn

class NotificationsViewModel(app: Application) : AndroidViewModel(app) {
    val notifications: StateFlow<List<NotificationEntity>> =
        AppDatabase.get(app).notifications().observeRecent()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
}
