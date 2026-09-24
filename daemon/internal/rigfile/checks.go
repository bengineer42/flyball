package rigfile

import (
	"fmt"
	"sort"
	"strings"
)

// CheckError reports every rule violation found in a document, so `rig
// check` can print all of them instead of stopping at the first.
type CheckError struct {
	Messages []string
}

func (e *CheckError) Error() string {
	return strings.Join(e.Messages, "; ")
}

// CheckBusinessRules runs the cross-field rules that JSON Schema
// alone cannot express, each mirroring a check inside
// engine/src/flyball/runtime/config.py's RigConfig._consistent
// model-validator (mode="after"). Schema validation must already have
// passed -- these assume the document has the shape rig.schema.json
// requires (links/devices/controllers are maps, etc).
func CheckBusinessRules(document map[string]any) error {
	var messages []string
	messages = append(messages, checkUndeclaredLinks(document)...)
	messages = append(messages, checkSingleDefaultController(document)...)
	messages = append(messages, checkClockOnlySimulated(document)...)
	messages = append(messages, checkInputCycles(document)...)
	if len(messages) > 0 {
		return &CheckError{Messages: messages}
	}
	return nil
}

func asMap(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

// checkUndeclaredLinks mirrors RigConfig._consistent's per-device check:
//
//	link = entry.driver_config.get("link")
//	if isinstance(link, str) and link not in self.links:
//	    raise ValueError(f"link {link!r} is not declared; links are {sorted(self.links)}")
func checkUndeclaredLinks(document map[string]any) []string {
	links := asMap(document["links"])
	devices := asMap(document["devices"])
	names := make([]string, 0, len(links))
	for name := range links {
		names = append(names, name)
	}
	sort.Strings(names)

	deviceNames := make([]string, 0, len(devices))
	for name := range devices {
		deviceNames = append(deviceNames, name)
	}
	sort.Strings(deviceNames)

	var messages []string
	for _, name := range deviceNames {
		entry := asMap(devices[name])
		link, ok := entry["link"].(string)
		if !ok {
			continue
		}
		if _, declared := links[link]; !declared {
			messages = append(messages, fmt.Sprintf(
				"device %q: link %q is not declared; links are %v", name, link, names,
			))
		}
	}
	return messages
}

// checkSingleDefaultController mirrors RigConfig._consistent's:
//
//	if sum(c.default for c in self.controllers.values()) > 1:
//	    raise ValueError("only one controller can be the default")
func checkSingleDefaultController(document map[string]any) []string {
	controllers := asMap(document["controllers"])
	count := 0
	for _, raw := range controllers {
		c := asMap(raw)
		if d, ok := c["default"].(bool); ok && d {
			count++
		}
	}
	if count > 1 {
		return []string{"only one controller can be the default"}
	}
	return nil
}

// checkClockOnlySimulated mirrors RigConfig._consistent's:
//
//	if self.clock is not None and not is_simulated(self.links):
//	    raise ValueError("`clock` is only for a rig whose links are all sim_* or fake_*")
//
// is_simulated (flyball.runtime.config.is_simulated) checks every link's
// type_name class attribute starts with "sim_" or "fake_". That attribute
// is set from each config's own `type` discriminator field
// (Config.union/Field(discriminator="type") in engine/src/flyball/model/
// config.py), which is what actually appears in the document -- e.g.
// `links: {chamber: {type: sim_plant, ...}}`.
func checkClockOnlySimulated(document map[string]any) []string {
	if document["clock"] == nil {
		return nil
	}
	links := asMap(document["links"])
	for name, raw := range links {
		link := asMap(raw)
		typ, _ := link["type"].(string)
		if typ == "" || (!strings.HasPrefix(typ, "sim_") && !strings.HasPrefix(typ, "fake_")) {
			return []string{fmt.Sprintf(
				"`clock` is only for a rig whose links are all sim_* or fake_* (link %q is %q)",
				name, typ,
			)}
		}
	}
	return nil
}

// checkInputCycles mirrors RigConfig._consistent's _refuse_input_cycles: a
// device that follows itself through `inputs:` -- directly, or through other
// devices -- is refused, the path named. Device by device, by the first
// segment of each address; a number binding follows nothing. The first cycle
// found is reported (the others are the same one seen from another device).
func checkInputCycles(document map[string]any) []string {
	devices := asMap(document["devices"])
	names := make([]string, 0, len(devices))
	for name := range devices {
		names = append(names, name)
	}
	sort.Strings(names)
	type edge struct{ input, source, target string }
	follows := map[string][]edge{}
	for _, name := range names {
		inputs := asMap(asMap(devices[name])["inputs"])
		keys := make([]string, 0, len(inputs))
		for key := range inputs {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			source, ok := inputs[key].(string)
			if !ok {
				continue
			}
			target, _, _ := strings.Cut(source, ".")
			if _, known := devices[target]; known {
				follows[name] = append(follows[name], edge{key, source, target})
			}
		}
	}
	type frame struct {
		name string
		path []string
	}
	for _, start := range names {
		stack := []frame{{start, nil}}
		seen := map[string]bool{}
		for len(stack) > 0 {
			top := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			for _, e := range follows[top.name] {
				step := append(append([]string{}, top.path...),
					fmt.Sprintf("%s.inputs.%s <- %s", top.name, e.input, e.source))
				if e.target == start {
					return []string{"a cycle through inputs: " + strings.Join(step, "; ")}
				}
				if !seen[e.target] {
					seen[e.target] = true
					stack = append(stack, frame{e.target, step})
				}
			}
		}
	}
	return nil
}
