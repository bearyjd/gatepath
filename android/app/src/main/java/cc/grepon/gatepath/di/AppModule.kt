package cc.grepon.gatepath.di

import android.content.Context
import android.net.ConnectivityManager
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
        connectivityManager: ConnectivityManager,
        probe: PortalProbe,
    ): CaptivePortalMonitor = CaptivePortalMonitor(connectivityManager, probe)

    @Provides
    @Singleton
    fun providePortalSessionManager(): PortalSessionManager = PortalSessionManager()
}
