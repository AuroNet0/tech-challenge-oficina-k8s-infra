package oficina.edge;

public final class AuthDatabaseBootstrapTest {
    public static void main(String[] arguments) throws Exception {
        String synthetic = "synthetic_test_value_only_1234567890";
        String valid = "{\"username\":\"oficina_auth\",\"password\":\"" + synthetic + "\"}";
        if (!synthetic.equals(AuthDatabaseBootstrap.password(valid, "oficina_auth"))) {
            throw new AssertionError("Valid synthetic credentials rejected");
        }
        String[] invalid = {
                null, "{}", "[]", "null", "{", valid + "x",
                valid.replace("oficina_auth", "oficina_auth_migrator"),
                valid.replace(synthetic, "short"),
                valid.replace(synthetic, synthetic + "\\n"),
                valid.replace("\"username\":", "\"extra\":true,\"username\":"),
                valid.replace("\"username\":", "\"username\":\"other\",\"username\":")
        };
        int rejected = 0;
        for (String document : invalid) {
            try {
                AuthDatabaseBootstrap.password(document, "oficina_auth");
            } catch (Exception expected) {
                rejected++;
            }
        }
        if (rejected != invalid.length) { throw new AssertionError("Invalid credentials accepted"); }
        System.out.println("Bootstrap parser: 1 valid and " + rejected + " invalid synthetic documents verified; no network.");
    }
}
