package com.ventouxlabs.gatepath.di

import com.ventouxlabs.gatepath.network.AndroidProcessBinding
import com.ventouxlabs.gatepath.network.ProcessBinding
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

/**
 * [com.ventouxlabs.gatepath.GatepathApplication] is not an `@AndroidEntryPoint`
 * (that annotation is for Activities/Services/Fragments/Views) and Hilt does
 * not field-inject the `@HiltAndroidApp` Application class itself, so
 * [ProcessBinding] — a `@Singleton` from [AppModule] — is fetched via this
 * entry point instead of `@Inject`.
 */
@EntryPoint
@InstallIn(SingletonComponent::class)
interface ProcessBindingEntryPoint {
    fun processBinding(): AndroidProcessBinding
}
