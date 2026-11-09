package oficina.edge;

public final class BootstrapContractTest {
    public static void main(String[] arguments) throws Exception {
        String syntheticValue = "synthetic_fixture_value_only_1234567890";
        String document = "{\"username\":\"oficina_auth\",\"password\":\"" + syntheticValue + "\"}";
        if (!syntheticValue.equals(AuthDatabaseBootstrap.password(document, "oficina_auth"))) {
            throw new AssertionError("BOOTSTRAP_VALID_DOCUMENT_REJECTED");
        }
        String[] invalid = {
            "invalid", "null", "{}", document + "{}",
            document.replace("oficina_auth", "foreign_user"),
            document.replace(syntheticValue, "short"),
            document.replace("}", ",\"extra\":true}"),
            document.replace("}", ",\"username\":\"oficina_auth\"}")
        };
        for (String candidate : invalid) {
            boolean rejected = false;
            try {
                AuthDatabaseBootstrap.password(candidate, "oficina_auth");
            } catch (Exception exception) {
                rejected = true;
            }
            if (!rejected) {
                throw new AssertionError("BOOTSTRAP_INVALID_DOCUMENT_ACCEPTED");
            }
        }
        System.out.println("Bootstrap helper: 9 offline JSON checks passed; values suppressed.");
    }
}
