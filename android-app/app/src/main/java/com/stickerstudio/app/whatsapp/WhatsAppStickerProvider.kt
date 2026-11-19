package com.stickerstudio.app.whatsapp

import android.content.ContentProvider
import android.content.ContentValues
import android.content.UriMatcher
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import java.io.File
import java.io.FileNotFoundException

/**
 * Android ContentProvider satisfying WhatsApp's official Sticker API specification.
 * When WhatsApp receives the "Add Sticker Pack" intent, it queries this provider
 * to read pack metadata and fetch individual sticker/tray assets.
 */
class WhatsAppStickerProvider : ContentProvider() {

    private val uriMatcher = UriMatcher(UriMatcher.NO_MATCH).apply {
        addURI(AUTHORITY, "metadata", CODE_METADATA)
        addURI(AUTHORITY, "metadata/*", CODE_PACK_METADATA)
        addURI(AUTHORITY, "stickers/*", CODE_STICKERS)
        addURI(AUTHORITY, "stickers_asset/*/*", CODE_STICKER_ASSET)
    }

    override fun onCreate(): Boolean = true

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?
    ): Cursor? {
        val context = context ?: return null
        return when (uriMatcher.match(uri)) {
            CODE_METADATA -> getPacksCursor()
            CODE_PACK_METADATA -> {
                val packIdentifier = uri.lastPathSegment ?: return null
                getSinglePackCursor(packIdentifier)
            }
            CODE_STICKERS -> {
                val packIdentifier = uri.lastPathSegment ?: return null
                getStickersCursor(packIdentifier)
            }
            else -> null
        }
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor? {
        val segments = uri.pathSegments
        if (segments.size < 3) throw FileNotFoundException("Invalid URI: $uri")

        val packId = segments[1]
        val fileName = segments[2]
        val context = context ?: throw FileNotFoundException("Context is null")

        val file = File(File(context.filesDir, "sticker_packs/$packId"), fileName)
        if (!file.exists()) throw FileNotFoundException("Asset not found: ${file.absolutePath}")

        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun getType(uri: Uri): String? = when (uriMatcher.match(uri)) {
        CODE_METADATA -> "vnd.android.cursor.dir/vnd.$AUTHORITY.metadata"
        CODE_STICKERS -> "vnd.android.cursor.dir/vnd.$AUTHORITY.stickers"
        CODE_STICKER_ASSET -> "image/webp"
        else -> null
    }

    private fun getPacksCursor(): Cursor {
        val columns = arrayOf(
            "sticker_pack_identifier",
            "sticker_pack_name",
            "sticker_pack_publisher",
            "sticker_pack_icon",
            "android_play_store_link",
            "ios_app_store_link",
            "is_animated_pack",
            "image_data_version"
        )
        return MatrixCursor(columns)
    }

    private fun getSinglePackCursor(packId: String): Cursor {
        return getPacksCursor()
    }

    private fun getStickersCursor(packId: String): Cursor {
        val columns = arrayOf("sticker_file_name", "sticker_pack_animation_data", "sticker_emojis")
        return MatrixCursor(columns)
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0

    companion object {
        const val AUTHORITY = "com.stickerstudio.app.stickercontentprovider"
        private const val CODE_METADATA = 1
        private const val CODE_PACK_METADATA = 2
        private const val CODE_STICKERS = 3
        private const val CODE_STICKER_ASSET = 4
    }
}
