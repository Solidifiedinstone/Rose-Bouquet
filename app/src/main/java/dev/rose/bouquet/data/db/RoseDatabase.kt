package dev.rose.bouquet.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [
        SongEntity::class,
        AlbumEntity::class,
        WatchEntity::class,
        OpinionEntity::class,
        ChannelEntity::class,
        FeedEntity::class,
    ],
    version = 1,
    exportSchema = false,
)
abstract class RoseDatabase : RoomDatabase() {

    abstract fun music(): MusicDao
    abstract fun youtube(): YouTubeDao

    companion object {
        @Volatile private var instance: RoseDatabase? = null

        fun get(context: Context): RoseDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext, RoseDatabase::class.java, "rose-bouquet.db",
            )
                // The cache and the feed can always be rebuilt from the server,
                // and the history is the only thing here that cannot — but a
                // destructive migration on version 1 has nothing to destroy.
                // Real migrations arrive with version 2, before any release
                // that would make somebody lose a watch history.
                .fallbackToDestructiveMigration()
                .build()
                .also { instance = it }
        }
    }
}
