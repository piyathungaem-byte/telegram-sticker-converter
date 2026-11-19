package com.stickerstudio.app.domain.converter

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.os.Build
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import kotlin.math.max

/**
 * Converts images to WhatsApp-compliant static stickers:
 * - Exactly 512x512 pixels
 * - Preserves aspect ratio, centered on a transparent canvas
 * - WebP format
 * - File size <= 100 KB
 * Also generates 96x96 tray icon (<= 50 KB).
 */
object StaticStickerConverter {

    private const val WA_STICKER_PX = 512
    private const val WA_TRAY_PX = 96
    private const val WA_STATIC_MAX_BYTES = 100 * 1024L
    private const val WA_TRAY_MAX_BYTES = 50 * 1024L

    private val QUALITY_LADDER = listOf(90, 80, 70, 60, 50, 40)

    fun convertToSticker(rawBytes: ByteArray, outputFile: File): Boolean {
        val original = BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size) ?: return false
        val canvasBitmap = fitToCanvas(original, WA_STICKER_PX)

        for (quality in QUALITY_LADDER) {
            val stream = ByteArrayOutputStream()
            val format = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Bitmap.CompressFormat.WEBP_LOSSY
            } else {
                @Suppress("DEPRECATION")
                Bitmap.CompressFormat.WEBP
            }

            canvasBitmap.compress(format, quality, stream)
            val bytes = stream.toByteArray()

            if (bytes.size <= WA_STATIC_MAX_BYTES) {
                FileOutputStream(outputFile).use { it.write(bytes) }
                return true
            }
        }
        return false
    }

    fun createTrayIcon(rawBytes: ByteArray, outputFile: File): Boolean {
        val original = BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size) ?: return false
        val canvasBitmap = fitToCanvas(original, WA_TRAY_PX)

        val stream = ByteArrayOutputStream()
        canvasBitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)
        val bytes = stream.toByteArray()

        if (bytes.size <= WA_TRAY_MAX_BYTES) {
            FileOutputStream(outputFile).use { it.write(bytes) }
            return true
        }

        // Quantize / compress if too large
        for (q in QUALITY_LADDER) {
            stream.reset()
            val format = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Bitmap.CompressFormat.WEBP_LOSSY
            } else {
                @Suppress("DEPRECATION")
                Bitmap.CompressFormat.WEBP
            }
            canvasBitmap.compress(format, q, stream)
            val qBytes = stream.toByteArray()
            if (qBytes.size <= WA_TRAY_MAX_BYTES) {
                FileOutputStream(outputFile).use { it.write(qBytes) }
                return true
            }
        }
        return false
    }

    private fun fitToCanvas(source: Bitmap, targetPx: Int): Bitmap {
        val scale = targetPx.toFloat() / max(source.width, source.height)
        val scaledW = (source.width * scale).toInt().coerceAtLeast(1)
        val scaledH = (source.height * scale).toInt().coerceAtLeast(1)

        val scaled = Bitmap.createScaledBitmap(source, scaledW, scaledH, true)
        val output = Bitmap.createBitmap(targetPx, targetPx, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(output)

        val left = (targetPx - scaledW) / 2f
        val top = (targetPx - scaledH) / 2f
        canvas.drawBitmap(scaled, left, top, null)

        return output
    }
}
