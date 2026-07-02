package com.ventouxlabs.gatepath.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF1565C0),
    onPrimary = Color.White,
    secondary = Color(0xFF0277BD),
    onSecondary = Color.White,
    surface = Color(0xFFF8F9FA),
    onSurface = Color(0xFF1A1C1E),
    background = Color(0xFFFFFFFF),
    onBackground = Color(0xFF1A1C1E),
    error = Color(0xFFB00020),
    onError = Color.White,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF90CAF9),
    onPrimary = Color(0xFF003064),
    secondary = Color(0xFF81D4FA),
    onSecondary = Color(0xFF003549),
    surface = Color(0xFF1A1C1E),
    onSurface = Color(0xFFE2E2E6),
    background = Color(0xFF111316),
    onBackground = Color(0xFFE2E2E6),
    error = Color(0xFFCF6679),
    onError = Color(0xFF370617),
)

@Composable
fun GatepathTheme(
    darkTheme: Boolean = false,
    content: @Composable () -> Unit,
) {
    val colorScheme = if (darkTheme) DarkColors else LightColors

    MaterialTheme(
        colorScheme = colorScheme,
        content = content,
    )
}
