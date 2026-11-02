const application = db.getSiblingDB('oficina_execution');
if (process.env.LOCAL_MONGO_PHASE === 'anonymous') {
    application.fixture.findOne();
    quit(0);
}
if (!application.auth(process.env.MONGO_USERNAME, process.env.MONGO_PASSWORD)) quit(1);
if (process.env.LOCAL_MONGO_PHASE === 'transaction') {
    application.fixture.insertOne({ _id: 1 });
    const session = db.getMongo().startSession();
    session.startTransaction();
    session.getDatabase('oficina_execution').fixture.insertOne({ _id: 2 });
    session.commitTransaction();
    session.endSession();
}
if (application.fixture.countDocuments({}) !== 2) quit(1);
