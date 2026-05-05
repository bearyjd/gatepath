package cc.grepon.gatepath

import android.app.Application
import cc.grepon.gatepath.audit.AuditLog
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class GatepathApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        AuditLog.init(filesDir)
    }
}
