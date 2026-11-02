import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import java.util.Base64;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

class CheckStores {
    public static void main(String[] arguments) {
        try {
            var input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
            int count = Integer.parseInt(input.readLine());
            for (int index = 0; index < count; index++) {
                String service = input.readLine();
                char[] password = input.readLine().toCharArray();
                var identity = KeyStore.getInstance("PKCS12");
                identity.load(new ByteArrayInputStream(Base64.getDecoder().decode(input.readLine())), password);
                var trust = KeyStore.getInstance("PKCS12");
                trust.load(new ByteArrayInputStream(Base64.getDecoder().decode(input.readLine())), password);
                int expectedRoots = Integer.parseInt(input.readLine());
                var inbound = KeyStore.getInstance("PKCS12");
                inbound.load(new ByteArrayInputStream(Base64.getDecoder().decode(input.readLine())), password);
                if (inbound.size() != 1) { throw new IllegalStateException(); }
                if (identity.size() != 1 || !identity.isKeyEntry(service) || trust.size() != expectedRoots) {
                    throw new IllegalStateException();
                }
                for (var aliases = trust.aliases(); aliases.hasMoreElements();) {
                    if (!trust.isCertificateEntry(aliases.nextElement())) { throw new IllegalStateException(); }
                }
                var keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
                keys.init(identity, password);
                var roots = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
                roots.init(trust);
                if (((X509TrustManager) roots.getTrustManagers()[0]).getAcceptedIssuers().length != expectedRoots) {
                    throw new IllegalStateException();
                }
                if (service.equals("os-service") && !Arrays.equals(password,
                        System.getProperty("javax.net.ssl.keyStorePassword", "").toCharArray())) {
                    throw new IllegalStateException();
                }
                Arrays.fill(password, '\0');
            }
            System.out.println("PASS Java 21 PKCS12 and argument file");
        } catch (Exception failure) {
            System.err.println("JAVA_PKCS12_REJECTED");
            System.exit(1);
        }
    }
}
