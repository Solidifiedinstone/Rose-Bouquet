package dev.rose.bouquet.youtube

/**
 * What the user wants to see, and what they have said they never want to.
 *
 * The point of this file is that **blocking actually blocks**. A great deal of
 * "tune your feed" in other apps is a hint that competes with engagement
 * signals and loses. Here [keep] is a filter applied after ranking: a blocked
 * word means the item is gone, not demoted.
 *
 * Ported from the desktop app's `core/interests.py`, values included, so the
 * phone and the desktop agree about what counts as slop.
 */
data class Interests(
    val wanted: Set<String> = emptySet(),
    val blocked: Set<String> = emptySet(),
    val blockedChannels: Set<String> = emptySet(),
    val filterSlop: Boolean = true,
) {
    val isEmpty: Boolean
        get() = wanted.isEmpty() && blocked.isEmpty() && blockedChannels.isEmpty() && !filterSlop
}

/**
 * Words too common to say anything about taste.
 *
 * Includes the vocabulary of engagement bait — "insane", "crazy", "you won't
 * believe" — because those words describe the thumbnail rather than the
 * subject, and letting them become topics is how a feed ends up recommending
 * hyperbole rather than a subject.
 */
private val STOPWORDS = setOf(
    "4k", "60fps", "a", "about", "actually", "after", "again", "all", "am", "amazing",
    "and", "anything", "are", "as", "at", "audio", "back", "bad", "be", "been", "before",
    "best", "better", "big", "but", "by", "can", "crazy", "day", "days", "did", "do",
    "does", "ep", "episode", "ever", "every", "everything", "feat", "first", "for",
    "from", "ft", "full", "get", "goes", "going", "good", "got", "guy", "guys", "hd",
    "he", "her", "here", "his", "how", "huge", "i", "if", "in", "insane", "into", "is",
    "it", "its", "just", "last", "like", "literally", "little", "live", "lol", "lyrics",
    "made", "make", "makes", "man", "many", "me", "more", "most", "much", "music", "my",
    "never", "new", "next", "no", "not", "nothing", "now", "of", "official", "old", "omg",
    "on", "one", "or", "our", "out", "over", "part", "part1", "people", "pt", "reaction",
    "really", "review", "she", "short", "shorts", "situation", "so", "someone",
    "something", "still", "subscribe", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "thing", "things", "this", "those", "time", "times", "to",
    "top", "two", "up", "video", "vol", "vs", "was", "watch", "way", "ways", "we", "went",
    "were", "what", "when", "where", "which", "who", "why", "will", "with", "worse",
    "worst", "wtf", "year", "years", "yes", "you", "your", "yours", "youtube",
)

/**
 * Phrases that mark engagement bait and generated filler.
 *
 * Matched as substrings of the lowercased title, so "AI cover" catches
 * "AI COVER" and "(ai cover)" alike. Kept short and specific on purpose: a
 * broad list catches things somebody genuinely wanted, and a filter that eats
 * real results gets turned off, which protects nothing.
 */
private val SLOP = setOf(
    "ai cover", "ai generated", "ai music", "ai voice", "ai-generated", "chatgpt",
    "edging", "elevenlabs", "gone sexual", "gone wrong", "gooner", "gooning",
    "hot girl", "hot girls", "made with ai", "midjourney", "must watch", "nsfw",
    "oddly satisfying", "onlyfans", "part 999", "reddit stories", "satisfying video",
    "sexy", "shocking truth", "sora", "text to speech", "thicc", "tier list of girls",
    "tts story", "veo", "waifu ranking", "wait for it", "watch till the end",
    "wont believe", "you won't believe",
)

/**
 * The slop phrases as one alternation, anchored on word boundaries.
 *
 * Plain substring matching is what a first pass reaches for and it is wrong:
 * `sora` then fires inside "Sorabji", `veo` inside "Veolia", `tts` inside
 * "watts". A filter that silently eats a piano recital because a generative
 * video model shares four of its letters is worse than no filter, because the
 * user never finds out why the thing they wanted never appeared.
 *
 * Built once rather than per title — this runs over every candidate in a feed.
 */
private val SLOP_PATTERN = Regex(
    SLOP.sortedByDescending { it.length }
        .joinToString("|") { Regex.escape(it) }
        .let { """(?<![\p{L}\p{N}])(?:$it)(?![\p{L}\p{N}])""" },
    RegexOption.IGNORE_CASE,
)

private const val MIN_WORD = 3

/**
 * The meaningful words in a title.
 *
 * Apostrophes are stripped rather than split on, so "don't" becomes "dont"
 * instead of "don" and "t" — two junk tokens where there was one word.
 */
fun words(text: String): List<String> = text.lowercase()
    .replace("'", "")
    .replace("’", "")
    .split(Regex("[^a-z0-9À-ɏ]+"))
    .filter { it.length >= MIN_WORD && it !in STOPWORDS && it.toIntOrNull() == null }

/** Whether a title reads as engagement bait or generated filler. */
fun isSlop(title: String): Boolean = SLOP_PATTERN.containsMatchIn(title) || isTagSpam(title)

/**
 * Titles that are a pile of hashtags rather than a description.
 *
 * "#batidao #brazilfunk #music #vibe #shorts #viral #fyp #cover #2026 #trend"
 * is a real result from a one-word search. A title like this is not about
 * anything — it is reaching for every feed at once, and it is most of what a
 * generic shorts search returns. Two or three tags at the end of a real title
 * are normal, so the test is whether the tags have crowded out the words.
 */
fun isTagSpam(title: String): Boolean {
    val tags = title.count { it == '#' }
    if (tags < TAG_LIMIT) return false

    val withoutTags = title.replace(Regex("#\\S+"), " ").trim()
    // Nothing left once the tags are removed, or the tags outnumber the words.
    return withoutTags.length < title.length / 3 ||
        withoutTags.split(Regex("\\s+")).count { it.isNotBlank() } <= tags
}

private const val TAG_LIMIT = 4

/**
 * Apply the user's stated preferences to a list of candidates.
 *
 * Order matters: blocked channels first (cheapest and most absolute), then
 * blocked words, then slop. Nothing here promotes — wanting a topic is handled
 * in ranking, where it belongs. This function only ever removes.
 */
fun <T> keep(
    candidates: List<T>,
    interests: Interests,
    title: (T) -> String,
    channel: (T) -> String,
): List<T> {
    if (interests.isEmpty) return candidates

    val blockedChannels = interests.blockedChannels.map { it.lowercase() }
    val blocked = interests.blocked.map { it.lowercase() }

    return candidates.filter { candidate ->
        val name = channel(candidate).lowercase()
        if (blockedChannels.any { it.isNotBlank() && it in name }) return@filter false

        val text = title(candidate).lowercase()
        if (blocked.any { it.isNotBlank() && it in text }) return@filter false

        !(interests.filterSlop && isSlop(text))
    }
}

/**
 * The topics a history is actually about.
 *
 * Words are counted across titles and the commonest kept, which is a blunt
 * instrument that has one large virtue over anything cleverer: it is legible.
 * The user can look at the list, see "commercial clothes steam press" sitting
 * there because of one odd video watched once, and remove it.
 */
fun deriveTopics(titles: List<String>, limit: Int = 24): List<String> {
    val counts = mutableMapOf<String, Int>()
    titles.forEach { title ->
        // Distinct per title, so one video repeating a word ten times counts
        // once rather than looking like ten videos about it.
        words(title).distinct().forEach { counts[it] = (counts[it] ?: 0) + 1 }
    }
    return counts.entries
        .filter { it.value > 1 }
        .sortedWith(compareByDescending<Map.Entry<String, Int>> { it.value }.thenBy { it.key })
        .take(limit)
        .map { it.key }
}
