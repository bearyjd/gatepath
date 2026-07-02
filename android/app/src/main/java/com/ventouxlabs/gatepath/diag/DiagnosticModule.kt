package com.ventouxlabs.gatepath.diag

import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

/**
 * Hilt provider for [DiagnosticEngine] + the Phase 1 probe set.
 *
 * The probe list is the only place phase membership is enforced — adding a
 * probe to this list adds it to the engine's parallel battery. Tests construct
 * [DiagnosticEngine] directly with their own probe list, so this module is
 * production-only.
 */
@Module
@InstallIn(SingletonComponent::class)
object DiagnosticModule {

    @Provides
    @Singleton
    fun provideDiagnosticEngine(): DiagnosticEngine = DiagnosticEngine(
        probes = listOf(
            PrivateDnsProbe(),
            HttpProbe(),
        ),
    )
}
