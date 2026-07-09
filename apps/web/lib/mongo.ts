import { MongoClient, Db } from "mongodb";

// Serverless pattern: initialize OUTSIDE the handler for connection reuse.
// See docs/reference/mongodb-connection.md
const uri = process.env.MONGODB_URI!;
const dbName = process.env.MONGODB_DB || "hyperadar";

if (!uri) throw new Error("MONGODB_URI not set");

const client = new MongoClient(uri, {
  maxPoolSize: 5,        // small pool per serverless instance
  minPoolSize: 0,        // no idle connections between warm invocations
  maxIdleTimeMS: 15000,  // release fast in serverless
  connectTimeoutMS: 10000,
  socketTimeoutMS: 30000,
});

let _db: Db | null = null;

export async function getDb(): Promise<Db> {
  if (!_db) {
    await client.connect();
    _db = client.db(dbName);
  }
  return _db;
}

export { client };
