package com.stickerstudio.app.whatsapp

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri

object WhatsAppIntentHelper {

    private const val EXTRA_STICKER_PACK_ID = "sticker_pack_id"
    private const val EXTRA_STICKER_PACK_AUTHORITY = "sticker_pack_authority"
    private const val EXTRA_STICKER_PACK_NAME = "sticker_pack_name"

    const val ADD_PACK_REQUEST_CODE = 200

    private const val WA_PACKAGE_NAME = "com.whatsapp"
    private const val WA_BUSINESS_PACKAGE_NAME = "com.whatsapp.w4b"

    fun isWhatsAppInstalled(context: Context): Boolean {
        val pm = context.packageManager
        return isPackageInstalled(pm, WA_PACKAGE_NAME) || isPackageInstalled(pm, WA_BUSINESS_PACKAGE_NAME)
    }

    private fun isPackageInstalled(pm: PackageManager, packageName: String): Boolean {
        return try {
            pm.getPackageInfo(packageName, PackageManager.GET_ACTIVITIES)
            true
        } catch (_: PackageManager.NameNotFoundException) {
            false
        }
    }

    /**
     * Launches the native WhatsApp intent to add the sticker pack directly.
     */
    fun launchAddToWhatsAppIntent(
        activity: Activity,
        packIdentifier: String,
        packName: String,
        authority: String = WhatsAppStickerProvider.AUTHORITY
    ): Boolean {
        val intent = Intent().apply {
            action = "com.whatsapp.intent.action.ENABLE_STICKER_PACK"
            putExtra(EXTRA_STICKER_PACK_ID, packIdentifier)
            putExtra(EXTRA_STICKER_PACK_AUTHORITY, authority)
            putExtra(EXTRA_STICKER_PACK_NAME, packName)
        }

        return try {
            activity.startActivityForResult(intent, ADD_PACK_REQUEST_CODE)
            true
        } catch (_: ActivityNotFoundException) {
            false
        }
    }
}
