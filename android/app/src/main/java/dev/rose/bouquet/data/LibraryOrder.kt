package dev.rose.bouquet.data

import dev.rose.bouquet.data.db.SongEntity

/**
 * How the library can be ordered, and what to call each one.
 *
 * The same set the desktop offers, so the two halves of Rose Bouquet sort a
 * library the same way — with one exception. The desktop has "Most played",
 * and this does not: a Subsonic library has no play count of its own, and
 * inventing one from what is on the phone would order your library by what you
 * happened to download. Starred is the honest equivalent and is offered
 * instead.
 *
 * The saved value is the enum name, so relabelling one of these later does not
 * silently change what a phone is already sorting by. A name this version does
 * not know falls back to the default rather than failing.
 */
enum class LibraryOrder(val label: String) {
    ArtistAsc("Artist A–Z"),
    ArtistDesc("Artist Z–A"),
    TitleAsc("Title A–Z"),
    TitleDesc("Title Z–A"),
    Album("Album"),
    Longest("Longest first"),
    Shortest("Shortest first"),
    RecentlyAdded("Recently added"),
    Starred("Starred first"),
    ;

    companion object {
        val Default = ArtistAsc

        /** The saved name, or the default if it is one we do not know. */
        fun of(name: String?): LibraryOrder =
            entries.firstOrNull { it.name == name } ?: Default
    }
}

/**
 * A song's place in the artist ordering, used as the tie-break for every other.
 *
 * Two songs of the same length or the same star still come out grouped by the
 * artist and album they belong to, rather than in whatever order the database
 * happened to hand them over.
 */
private fun artistKey(song: SongEntity): List<Comparable<*>> = listOf(
    song.artist.lowercase(),
    song.album.lowercase(),
    song.track ?: 0,
    song.title.lowercase(),
)

private val byArtist = compareBy<SongEntity>(
    { it.artist.lowercase() },
    { it.album.lowercase() },
    { it.track ?: 0 },
    { it.title.lowercase() },
)

private val byTitle = compareBy<SongEntity>(
    { it.title.lowercase() },
    { it.artist.lowercase() },
)

/**
 * These songs in the order asked for.
 *
 * A track with no duration read yet sorts as unknown rather than as a
 * zero-second song, which would otherwise put every unread file at the top of
 * shortest-first.
 */
fun List<SongEntity>.inOrder(order: LibraryOrder): List<SongEntity> = when (order) {
    LibraryOrder.ArtistAsc -> sortedWith(byArtist)
    LibraryOrder.ArtistDesc -> sortedWith(byArtist.reversed())
    LibraryOrder.TitleAsc -> sortedWith(byTitle)
    LibraryOrder.TitleDesc -> sortedWith(byTitle.reversed())
    LibraryOrder.Album -> sortedWith(
        compareBy<SongEntity>({ it.album.lowercase() }, { it.track ?: 0 }, { it.title.lowercase() })
    )
    LibraryOrder.Longest -> sortedWith(
        compareByDescending<SongEntity> { it.durationSeconds }.then(byArtist)
    )
    LibraryOrder.Shortest -> sortedWith(
        compareBy<SongEntity> { if (it.durationSeconds > 0) it.durationSeconds else Int.MAX_VALUE }
            .then(byArtist)
    )
    LibraryOrder.RecentlyAdded -> sortedWith(
        compareByDescending<SongEntity> { it.seenAt }.then(byArtist)
    )
    LibraryOrder.Starred -> sortedWith(
        compareByDescending<SongEntity> { it.starred }.then(byArtist)
    )
}
