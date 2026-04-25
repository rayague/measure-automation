# measure-automation

## Step 01: Collect traces from Jaeger

1. Edit `config/settings.yaml`
   - Set `service_name` to a real service name from Jaeger UI.

2. Install packages:

```powershell
python -m pip install requests pandas pyyaml
```

3. Run:

```powershell
python .\src\boundary_analyzer\pipeline\step_01_collect_traces.py
```
