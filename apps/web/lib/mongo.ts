import { type ClientSession, Db, MongoClient } from "mongodb";

// Serverless pattern: initialize OUTSIDE the handler for connection reuse.
// See docs/reference/mongodb-connection.md
let _db: Db | null = null;
let client: MongoClient | null = null;

export async function getDb(): Promise<Db> {
	if (_db) return _db;

	const uri = process.env.MONGODB_URI;
	if (!uri) throw new Error("MONGODB_URI not set");

	client ??= new MongoClient(uri, {
		maxPoolSize: 5,
		minPoolSize: 0,
		maxIdleTimeMS: 15000,
		connectTimeoutMS: 10000,
		socketTimeoutMS: 30000,
	});
	await client.connect();
	_db = client.db(process.env.MONGODB_DB || "hyperadar");
	return _db;
}

export async function closeMongoConnection(): Promise<void> {
	await client?.close();
	client = null;
	_db = null;
}

export async function withMongoTransaction<T>(
	operation: (db: Db, session: ClientSession) => Promise<T>,
): Promise<T> {
	const db = await getDb();
	if (!client) throw new Error("MongoDB client unavailable");
	return client.withSession((session) =>
		session.withTransaction(() => operation(db, session)),
	);
}
