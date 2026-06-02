package cc.grepon.gatepath.di

import android.content.Context
import android.net.ConnectivityManager
import android.provider.Settings
import cc.grepon.gatepath.BuildConfig
import cc.grepon.gatepath.network.CONNECTIVITY_CHECK_URL
import cc.grepon.gatepath.network.CaptivePortalMonitor
import cc.grepon.gatepath.network.PortalProbe
import cc.grepon.gatepath.session.PortalSessionManager
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun provideConnectivityManager(
        @ApplicationContext context: Context,
    ): ConnectivityManager =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    @Provides
    @Singleton
    fun providePortalProbe(): PortalProbe = PortalProbe()

    @Provides
    @Singleton
    fun provideCaptivePortalMonitor(
        @ApplicationContext context: Context,
        connectivityManager: ConnectivityManager,
        probe: PortalProbe,
    ): CaptivePortalMonitor =
        CaptivePortalMonitor(connectivityManager, probe, resolveProbeUrl(context))

    /**
     * Debug builds honour the system's `captive_portal_http_url` override so
     * the Android e2e harness can aim Gatepath's own connectivity probe at its
     * mock portal instead of the hardcoded gstatic endpoint. Release builds
     * never read the setting — they always use [CONNECTIVITY_CHECK_URL].
     */
    private fun resolveProbeUrl(context: Context): String {
        if (!BuildConfig.DEBUG) return CONNECTIVITY_CHECK_URL
        val override = Settings.Global.getString(
            context.contentResolver,
            "captive_portal_http_url",
        )
        return override?.takeIf { it.isNotBlank() } ?: CONNECTIVITY_CHECK_URL
    }

    @Provides
    @Singleton
    fun providePortalSessionManager(): PortalSessionManager = PortalSessionManager()
}
