// Health checks initialize rs0 once and wait for a writable primary.
try {
  rs.status();
} catch (error) {
  if (error.code !== 94) throw error; // NotYetInitialized
  try {
    rs.initiate({ _id: "rs0", members: [{ _id: 0, host: "mongo:27017" }] });
  } catch (initError) {
    if (initError.code !== 23) throw initError; // AlreadyInitialized
  }
}
quit(db.hello().isWritablePrimary ? 0 : 1);
