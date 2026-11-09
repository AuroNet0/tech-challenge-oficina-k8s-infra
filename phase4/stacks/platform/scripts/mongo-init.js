try {
    const admin = db.getSiblingDB('admin');
    const rootUser = process.env.MONGO_ROOT_USERNAME;
    const rootPassword = process.env.MONGO_ROOT_PASSWORD;
    if (!rootUser || !rootPassword || !process.env.MONGO_PASSWORD || !process.env.MONGO_USERNAME) quit(1);
    let authenticated = false;
    try { authenticated = Boolean(admin.auth(rootUser, rootPassword)); } catch {}
    if (!authenticated) {
        try {
            rs.initiate({ _id: 'rs0', members: [{ _id: 0, host: process.env.MONGO_MEMBER }] });
        } catch (failure) {
            if (failure.code !== 23) throw failure;
        }
        if (!db.hello().isWritablePrimary) quit(1);
        admin.createUser({ user: rootUser, pwd: rootPassword, roles: [{ role: 'root', db: 'admin' }] });
        if (!admin.auth(rootUser, rootPassword)) quit(1);
    }
    if (!db.hello().isWritablePrimary || rs.conf()._id !== 'rs0' || rs.conf().members.length !== 1 || rs.conf().members[0].host !== process.env.MONGO_MEMBER) quit(1);
    const application = db.getSiblingDB(process.env.MONGO_DATABASE);
    const existing = application.getUser(process.env.MONGO_USERNAME);
    if (existing === null) {
        application.createUser({ user: process.env.MONGO_USERNAME, pwd: process.env.MONGO_PASSWORD,
            roles: [{ role: 'readWrite', db: process.env.MONGO_DATABASE }] });
    } else if (existing.roles.length !== 1 || existing.roles[0].role !== 'readWrite' || existing.roles[0].db !== process.env.MONGO_DATABASE) {
        quit(1);
    }
    quit(0);
} catch {
    print('MongoDB bootstrap incomplete; inspect credentials and replica-set configuration without logging secrets.');
    quit(1);
}
