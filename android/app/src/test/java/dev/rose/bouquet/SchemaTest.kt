package dev.rose.bouquet

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The migration against the schema Room actually generates.
 *
 * A migration creates indices by name, and Room checks on open that what is in
 * the database matches what it expected. Get a name wrong and the app throws
 * the first time anybody opens it after updating — for everybody at once, with
 * no way to recover but clearing the data that the migration existed to
 * protect. The names are derivable but easy to get subtly wrong, so they are
 * compared rather than assumed.
 */
class SchemaTest {

    private val schema: String by lazy {
        val file = File("schemas/dev.rose.bouquet.data.db.RoseDatabase/2.json")
        assertTrue("schema not exported — run an assemble first", file.exists())
        file.readText()
    }

    private val migration: String by lazy {
        File("src/main/java/dev/rose/bouquet/data/db/RoseDatabase.kt").readText()
    }

    @Test
    fun `every index the migration creates is one Room expects`() {
        val created = Regex("CREATE INDEX IF NOT EXISTS (\\w+)")
            .findAll(migration).map { it.groupValues[1] }.toList()

        assertTrue("the migration creates no indices — did it change?", created.isNotEmpty())
        created.forEach { name ->
            assertTrue("Room does not define an index called $name", "\"$name\"" in schema)
        }
    }

    @Test
    fun `the database version and the migration agree`() {
        assertTrue("schema 2.json is the version the code declares", "\"version\": 2" in schema)
        assertTrue("a migration to 2 exists", "Migration(1, 2)" in migration)
    }

    @Test
    fun `nothing falls back to dropping the tables`() {
        // The watch history is imported once from an archive somebody had to
        // request from Google. It must never be thrown away to avoid writing a
        // migration.
        assertTrue(
            "destructive migration would silently delete the watch history",
            "fallbackToDestructiveMigration" !in migration,
        )
    }
}
