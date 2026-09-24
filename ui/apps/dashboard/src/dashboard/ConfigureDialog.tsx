import { useMemo, useState } from "react";
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from "@mui/material";
import { Form as MuiForm } from "@rjsf/mui";
import { SchemaForm } from "@flyball/react";
import type { DashboardWidget } from "@flyball/client";
import { widgetType } from "../widgets/registry.js";
import { useBindings } from "./context.js";

/** The widget's label and its type's config form, live; Apply hands back the edited widget. Mounted per widget (keyed by the caller). */
export function ConfigureDialog({ widget, onClose, onApply }: { widget: DashboardWidget; onClose(): void; onApply(widget: DashboardWidget): void }) {
  const bindings = useBindings();
  const type = widgetType(widget.type);
  const [label, setLabel] = useState(widget.label ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(widget.config);
  // The schema may depend on the value (a device's commands follow the device picked), so it is rebuilt as the form changes.
  const schema = useMemo(() => type?.configSchema(bindings, config), [type, bindings, config]);
  const placeholder = type?.labelFor?.(config, bindings) ?? type?.label ?? widget.type;
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm" data-testid="configure-dialog">
      <DialogTitle>
        Configure {type?.label ?? widget.type}
        <Typography variant="body2" color="text.secondary" component="span" sx={{ ml: 1.5 }}>
          {widget.id}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <TextField label="Label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder={placeholder} helperText="Blank: the widget names itself." fullWidth sx={{ mt: 1.5, mb: 1.5 }} />
        {schema && type ? (
          <SchemaForm schema={schema} value={config} onChange={setConfig} uiSchema={type.uiSchema} form={MuiForm} idPrefix={`cfg-${widget.id}`} />
        ) : (
          <Typography color="text.secondary">Unknown widget type: nothing to configure.</Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => onApply({ ...widget, label: label.trim() || null, config })} data-testid="configure-apply">
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  );
}
