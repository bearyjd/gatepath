package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Modifier

class CertSummaryTest {

    @Test
    fun `fingerprint is lowercase hex sha256 of the der bytes`() {
        val s = CertSummary.of(3, 1L, 2L, subjectEqualsIssuer = true, derEncoded = byteArrayOf(1, 2, 3))
        assertEquals("039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81", s.sha256Fingerprint)
        assertTrue(s.selfSigned)
    }

    @Test
    fun `missing der yields an empty fingerprint not a crash`() {
        assertEquals("", CertSummary.of(0, null, null, false, null).sha256Fingerprint)
    }

    @Test
    fun `every field is an enum number date boolean or fingerprint`() {
        val declared = CertSummary::class.java.declaredFields
            .filterNot { Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertEquals(
            "CertSummary fields changed. Subject and issuer strings are gateway-authored and must never be added.",
            setOf("primaryError", "notBeforeEpochMillis", "notAfterEpochMillis", "selfSigned", "sha256Fingerprint"),
            declared,
        )
    }
}
