# Keep rules for the release build.
#
# The reason this file has content rather than being the empty stub AGP
# generates: NewPipeExtractor finds its services and its parsers reflectively,
# and Rhino — the JavaScript engine it uses to run YouTube's own signature
# code — loads classes by name at runtime. Shrinking without these produces a
# build that installs, runs, plays music, and fails only on YouTube, which is
# the worst shape a failure can take.

# ── NewPipeExtractor ──────────────────────────────────────────────
# Services are looked up through ServiceList and instantiated reflectively,
# and the extractors are found by class name from there.
-keep class org.schabi.newpipe.extractor.** { *; }
-dontwarn org.schabi.newpipe.extractor.**

# It targets desktop Java and references classes Android does not ship. These
# are on paths that Android never takes, so a warning is all they are.
-dontwarn java.awt.**
-dontwarn javax.annotation.**
-dontwarn javax.naming.**
-dontwarn org.slf4j.**

# ── Rhino ─────────────────────────────────────────────────────────
# Runs YouTube's signature-deciphering JavaScript. It resolves host classes
# by name, so nothing here can be renamed.
-keep class org.mozilla.javascript.** { *; }
-keep class org.mozilla.classfile.** { *; }
-dontwarn org.mozilla.javascript.**

# ── jsoup ─────────────────────────────────────────────────────────
# Used directly to parse Takeout exports.
-keep class org.jsoup.** { *; }
-dontwarn org.jsoup.**

# ── kotlinx.serialization ─────────────────────────────────────────
# Generated serializers are found reflectively from the companion.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class dev.rose.bouquet.**$$serializer { *; }
-keepclassmembers class dev.rose.bouquet.** {
    *** Companion;
    *** serializer(...);
}

# ── The app's own data classes ────────────────────────────────────
# Room entities and anything serialised by name.
-keep class dev.rose.bouquet.data.** { *; }

# ── Media3 ────────────────────────────────────────────────────────
-dontwarn androidx.media3.**

# Keep line numbers so a stack trace in a bug report means something.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
