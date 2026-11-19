package com.stickerstudio.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class TelegramResponse<T>(
    val ok: Boolean,
    val result: T? = null,
    val description: String? = null
)

@Serializable
data class TelegramStickerSet(
    val name: String,
    val title: String,
    @SerialName("is_animated") val isAnimated: Boolean = false,
    @SerialName("is_video") val isVideo: Boolean = false,
    val stickers: List<TelegramStickerItem> = emptyList()
)

@Serializable
data class TelegramStickerItem(
    @SerialName("file_id") val fileId: String,
    @SerialName("file_unique_id") val fileUniqueId: String,
    val width: Int = 512,
    val height: Int = 512,
    @SerialName("is_animated") val isAnimated: Boolean = false,
    @SerialName("is_video") val isVideo: Boolean = false,
    val emoji: String = "📎",
    @SerialName("file_size") val fileSize: Long? = null
)

@Serializable
data class TelegramFileResponse(
    @SerialName("file_id") val fileId: String,
    @SerialName("file_unique_id") val fileUniqueId: String,
    @SerialName("file_path") val filePath: String? = null,
    @SerialName("file_size") val fileSize: Long? = null
)
