package com.expensetracker.notif.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface NotificationDao {
    @Insert
    suspend fun insert(entity: NotificationEntity): Long

    @Query("SELECT * FROM notifications ORDER BY postedAt DESC LIMIT 500")
    fun observeRecent(): Flow<List<NotificationEntity>>
}
