package dev.rose.bouquet.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [
        SongEntity::class,
        AlbumEntity::class,
        WatchEntity::class,
        OpinionEntity::class,
        ChannelEntity::class,
        FeedEntity::class,
    ],
    version = 2,
    exportSchema = true,
)
abstract class RoseDatabase : RoomDatabase() {

    abstract fun music(): MusicDao
    abstract fun youtube(): YouTubeDao

    companion object {
        @Volatile private var instance: RoseDatabase? = null

        /**
         * Indices for the columns the library and shorts queries sort on.
         *
         * Data is untouched — `CREATE INDEX` only builds a lookup structure —
         * so nothing here can lose a watch history.
         */
        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS index_songs_serverId_artist_album_track " +
                        "ON songs (serverId, artist, album, track)"
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS index_watch_history_watchedAt " +
                        "ON watch_history (watchedAt)"
                )
            }
        }

        fun get(context: Context): RoseDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext, RoseDatabase::class.java, "rose-bouquet.db",
            )
                // No destructive fallback.
                //
                // The library cache and the feed can be rebuilt from a server
                // in a minute. The watch history cannot: it is imported once
                // from a Google Takeout archive somebody had to request, wait
                // for and download, and it is what every recommendation in the
                // app is built from. Dropping the tables to avoid writing a
                // migration would throw exactly the thing that is expensive to
                // replace, silently, on an ordinary update.
                //
                // A missing migration now fails loudly on open instead, which
                // is a bug caught in testing rather than data lost on a phone.
                .addMigrations(MIGRATION_1_2)
                .build()
                .also { instance = it }
        }
    }
}
