# Gatepath ProGuard / R8 rules
#
# We do NOT keep all classes in cc.grepon.gatepath — that defeats R8 entirely.
# R8 strips internal symbols freely; only reflection-based code paths are kept
# explicitly below.

# kotlinx.serialization runtime annotations + companion lookup.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# Generated $$serializer companions for our @Serializable data classes.
-keep,includedescriptorclasses class cc.grepon.gatepath.audit.**$$serializer { *; }
-keepclassmembers class cc.grepon.gatepath.audit.** {
    *** Companion;
}
-keepclasseswithmembers class cc.grepon.gatepath.audit.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# AuditEntry is reflected on at deserialization time.
-keep class cc.grepon.gatepath.audit.AuditEntry { *; }

# Hilt's own consumer-rules.pro covers @AndroidEntryPoint / @HiltAndroidApp
# generated classes; nothing else from cc.grepon.gatepath needs explicit keeping.
