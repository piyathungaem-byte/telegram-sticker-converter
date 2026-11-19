package com.stickerstudio.app.ui.screens.home

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavController
import com.stickerstudio.app.ui.theme.BorderColor
import com.stickerstudio.app.ui.theme.DarkBg
import com.stickerstudio.app.ui.theme.DarkCard
import com.stickerstudio.app.ui.theme.DarkSurface
import com.stickerstudio.app.ui.theme.Mint
import com.stickerstudio.app.ui.theme.MintBg
import com.stickerstudio.app.ui.theme.TextMuted
import com.stickerstudio.app.ui.theme.TextPrimary
import com.stickerstudio.app.ui.theme.TextSecondary

@Composable
fun HomeScreen(navController: NavController) {
    var packLink by remember { mutableStateOf("") }
    var botToken by remember { mutableStateOf("") }
    var rememberToken by remember { mutableStateOf(true) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(DarkBg)
            .verticalScroll(rememberScrollState())
            .padding(20.dp)
    ) {
        Text(
            text = "STICKER STUDIO",
            fontSize = 11.sp,
            fontWeight = FontWeight.ExtraBold,
            color = Mint,
            letterSpacing = 1.sp
        )
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            text = "Convert Telegram stickers to WhatsApp",
            fontSize = 26.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary,
            lineHeight = 30.sp
        )
        Text(
            text = "Fetch, preview, select, and import — all on your phone.",
            fontSize = 14.sp,
            color = TextMuted,
            modifier = Modifier.padding(top = 4.dp, bottom = 20.dp)
        )

        // Main Card
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(16.dp))
                .background(DarkCard)
                .border(1.dp, BorderColor, RoundedCornerShape(16.dp))
                .padding(16.dp)
        ) {
            Text(
                text = "Telegram sticker-pack link",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = TextSecondary
            )
            Spacer(modifier = Modifier.height(6.dp))
            OutlinedTextField(
                value = packLink,
                onValueChange = { packLink = it },
                placeholder = { Text("https://t.me/addstickers/YourPack", color = TextMuted) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Mint,
                    unfocusedBorderColor = BorderColor,
                    focusedContainerColor = DarkSurface,
                    unfocusedContainerColor = DarkSurface,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary
                )
            )

            Spacer(modifier = Modifier.height(14.dp))

            Text(
                text = "Bot token",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = TextSecondary
            )
            Spacer(modifier = Modifier.height(6.dp))
            OutlinedTextField(
                value = botToken,
                onValueChange = { botToken = it },
                placeholder = { Text("Paste token from @BotFather", color = TextMuted) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Mint,
                    unfocusedBorderColor = BorderColor,
                    focusedContainerColor = DarkSurface,
                    unfocusedContainerColor = DarkSurface,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary
                )
            )

            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(top = 8.dp, bottom = 14.dp)
            ) {
                Checkbox(
                    checked = rememberToken,
                    onCheckedChange = { rememberToken = it },
                    colors = CheckboxDefaults.colors(
                        checkedColor = Mint,
                        checkmarkColor = DarkBg
                    )
                )
                Text(
                    text = "Remember token (encrypted on device)",
                    fontSize = 12.sp,
                    color = TextSecondary
                )
            }

            Button(
                onClick = {
                    // Navigate to selection or start fetch
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = Mint,
                    contentColor = DarkBg
                )
            ) {
                Text(
                    text = "Fetch sticker pack",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold
                )
            }

            Text(
                text = "Token stays on your device. Never sent to any external service.",
                fontSize = 11.sp,
                color = TextMuted,
                modifier = Modifier.padding(top = 10.dp)
            )
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Fullyst button
        Button(
            onClick = { /* Navigate to Browse */ },
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = MintBg,
                contentColor = Mint
            )
        ) {
            Text(
                text = "Browse Fullyst catalog · no token needed",
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold
            )
        }

        Spacer(modifier = Modifier.height(24.dp))

        Text(
            text = "RECENT CONVERSIONS",
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            color = TextMuted,
            letterSpacing = 1.sp
        )
        Spacer(modifier = Modifier.height(8.dp))

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(14.dp))
                .background(DarkCard)
                .border(1.dp, BorderColor, RoundedCornerShape(14.dp))
                .padding(14.dp)
        ) {
            Text(
                text = "No recent packs yet.",
                fontSize = 13.sp,
                color = TextMuted
            )
        }
    }
}
