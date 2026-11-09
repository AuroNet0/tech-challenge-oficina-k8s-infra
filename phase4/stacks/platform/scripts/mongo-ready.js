try {
    const application = db.getSiblingDB(process.env.MONGO_DATABASE);
    if (!application.auth(process.env.MONGO_USERNAME, process.env.MONGO_PASSWORD)) quit(1);
    const hello = application.runCommand({ hello: 1 });
    quit(hello.isWritablePrimary && hello.setName === 'rs0' ? 0 : 1);
} catch {
    quit(1);
}
