import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.snakeyaml.engine.v2.api.Load;
import org.snakeyaml.engine.v2.api.LoadSettings;

public class ValidateWorkflows {
    public static void main(String[] arguments) throws Exception {
        var loader = new Load(LoadSettings.builder().setAllowDuplicateKeys(false).build());
        try (var workflows = Files.list(Path.of(".github/workflows"))) {
            for (var workflow : workflows.filter(file -> file.toString().endsWith(".yml")).toList()) {
                var document = (Map<?, ?>) loader.loadFromString(Files.readString(workflow));
                if (!(document.get("on") instanceof Map<?, ?>) || !(document.get("jobs") instanceof Map<?, ?>)) {
                    throw new IllegalStateException("Workflow requires on and jobs mappings: " + workflow);
                }
                System.out.println("YAML parsed, unique keys: " + workflow);
            }
        }
    }
}
