package com.stickerstudio.app.domain.model

enum class StickerFormat {
    STATIC_WEBP,
    ANIMATED_TGS,
    VIDEO_WEBM,
    ANIMATED_WEBP
}

data class StickerItem(
    val index: Int,
    val fileId: String,
    val uniqueId: String,
    val emoji: String,
    val format: StickerFormat,
    val localUri: String? = null,
    val isSelected: Boolean = true
)

data class ConvertedSticker(
    val index: Int,
    val fileName: String,
    val filePath: String,
    val isAnimated: Boolean,
    val sizeBytes: Long,
    val emojis: List<String> = listOf("😀")
)

data class WhatsAppPack(
    val identifier: String,
    val name: String,
    val publisher: String = "Sticker Studio",
    val trayImagePath: String,
    val stickers: List<ConvertedSticker>,
    val isAnimated: Boolean
)
