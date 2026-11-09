package oficina.edge;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.secretsmanager.SecretsManagerClient;
import software.amazon.awssdk.services.secretsmanager.model.GetSecretValueRequest;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Map;
import java.util.Set;

public final class AuthDatabaseBootstrap {
    private AuthDatabaseBootstrap() {}

    public static void main(String[] arguments) {
        try {
            var environment = System.getenv();
            var secretId = required(environment, "AUTH_DB_SECRET_ID");
            var scope = required(environment, "AUTH_ENVIRONMENT");
            if (!Set.of("demo-a", "demo-b").contains(scope)
                    || !secretId.equals("/oficina/phase4/" + scope + "/auth-postgres")
                    || !"oficina_auth".equals(required(environment, "AUTH_DB_USER"))
                    || !Files.isReadable(Path.of("/opt/auth-home/.postgresql/root.crt"))) {
                throw new IllegalArgumentException();
            }
            try (var client = SecretsManagerClient.builder()
                    .region(Region.of(required(environment, "AWS_REGION")))
                    .httpClientBuilder(UrlConnectionHttpClient.builder()
                            .connectionTimeout(Duration.ofSeconds(2)).socketTimeout(Duration.ofSeconds(2)))
                    .overrideConfiguration(config -> config.apiCallTimeout(Duration.ofSeconds(4))
                            .apiCallAttemptTimeout(Duration.ofSeconds(2))).build()) {
                String document = client.getSecretValue(GetSecretValueRequest.builder()
                        .secretId(secretId).versionStage("AWSCURRENT").build()).secretString();
                System.out.print(password(document, required(environment, "AUTH_DB_USER")));
            }
        } catch (Exception exception) {
            System.exit(1);
        }
    }

    static String password(String document, String expectedUser) throws Exception {
        if (document == null || document.length() > 4096) { throw new IllegalArgumentException(); }
        var root = JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .build().readTree(document);
        if (root == null || !root.isObject() || root.size() != 2
                || !root.path("username").isTextual() || !root.path("password").isTextual()
                || !expectedUser.equals(root.path("username").textValue())
                || !root.path("password").textValue().matches("[A-Za-z0-9_-]{32,128}")) {
            throw new IllegalArgumentException();
        }
        return root.path("password").textValue();
    }

    private static String required(Map<String, String> environment, String name) {
        var value = environment.get(name);
        if (value == null || value.isBlank()) { throw new IllegalArgumentException(); }
        return value;
    }
}
