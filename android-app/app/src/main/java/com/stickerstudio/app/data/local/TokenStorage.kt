package com.stickerstudio.app.data.local

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Stores Telegram Bot Token safely using Android Keystore encryption
 * (Equivalent to Windows DPAPI used in webapp.py).
 */
class TokenStorage(context: Context) {

    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context,
        "secure_sticker_prefs",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    fun saveToken(token: String) {
        prefs.edit().putString(KEY_BOT_TOKEN, token.trim()).apply()
    }

    fun getToken(): String? {
        return prefs.getString(KEY_BOT_TOKEN, null)
    }

    fun clearToken() {
        prefs.edit().remove(KEY_BOT_TOKEN).apply()
    }

    companion object {
        private const val KEY_BOT_TOKEN = "tg_bot_token"
    }
}
