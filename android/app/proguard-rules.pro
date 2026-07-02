# Gatepath ProGuard / R8 rules
#
# We do NOT keep all classes in com.ventouxlabs.gatepath — that defeats R8 entirely.
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
-keep,includedescriptorclasses class com.ventouxlabs.gatepath.audit.**$$serializer { *; }
-keepclassmembers class com.ventouxlabs.gatepath.audit.** {
    *** Companion;
}
-keepclasseswithmembers class com.ventouxlabs.gatepath.audit.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# AuditEntry is reflected on at deserialization time.
-keep class com.ventouxlabs.gatepath.audit.AuditEntry { *; }

# Hilt's own consumer-rules.pro covers @AndroidEntryPoint / @HiltAndroidApp
# generated classes; nothing else from com.ventouxlabs.gatepath needs explicit keeping.
