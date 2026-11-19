package com.stickerstudio.app.data.api

import com.stickerstudio.app.data.model.TelegramFileResponse
import com.stickerstudio.app.data.model.TelegramResponse
import com.stickerstudio.app.data.model.TelegramStickerSet
import okhttp3.ResponseBody
import retrofit2.http.GET
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Url

interface TelegramApiService {

    @GET("bot{token}/getStickerSet")
    suspend fun getStickerSet(
        @Path("token") token: String,
        @Query("name") setName: String
    ): TelegramResponse<TelegramStickerSet>

    @GET("bot{token}/getFile")
    suspend fun getFile(
        @Path("token") token: String,
        @Query("file_id") fileId: String
    ): TelegramResponse<TelegramFileResponse>

    @GET
    suspend fun downloadFile(
        @Url fileUrl: String
    ): ResponseBody
}
