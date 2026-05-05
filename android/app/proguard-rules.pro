# Gatepath ProGuard / R8 rules

# Keep Hilt entry points
-keep class cc.grepon.gatepath.** { *; }

# Keep kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class cc.grepon.gatepath.**$$serializer { *; }
-keepclassmembers class cc.grepon.gatepath.** {
    *** Companion;
}
-keepclasseswithmembers class cc.grepon.gatepath.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# Keep audit log data classes (serialized via kotlinx.serialization)
-keep class cc.grepon.gatepath.audit.AuditEntry { *; }
