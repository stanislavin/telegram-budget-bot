package com.expensetracker.notif.data

import android.content.Context
import android.content.SharedPreferences

object AppFilterPrefs {

    private const val PREFS_NAME = "app_filter"
    private const val KEY_ALLOWED = "allowed_packages"

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** Returns the set of allowed package names, or empty set meaning "all allowed". */
    fun getAllowed(context: Context): Set<String> =
        prefs(context).getStringSet(KEY_ALLOWED, emptySet()) ?: emptySet()

    fun setAllowed(context: Context, packages: Set<String>) {
        prefs(context).edit().putStringSet(KEY_ALLOWED, packages).apply()
    }

    /** Returns true if the given package should be captured (empty filter = allow all). */
    fun isAllowed(context: Context, packageName: String): Boolean {
        val allowed = getAllowed(context)
        return allowed.isEmpty() || packageName in allowed
    }
}
