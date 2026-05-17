import re
import json
import base64
import zstandard as zstd
import sys
import os

class SKLL_Engine:
    STDLIB = """
    #macro wait(secs) { !INTERNAL_ACTION("WAIT", "VALUE", secs); }
    #macro skill(skill_name, speed, start_time, cancel_last, enable_variants, hold_for) { !INTERNAL_ACTION("SKILL", "MOVE", skill_name, "SPEED", speed, "START", start_time, "CANCEL LAST", cancel_last, "ENABLE VARIANTS", enable_variants, "HOLD FOR", hold_for); }
    #macro velocity(force, time, track, fade, ragdoll, relative_to_origin) { !INTERNAL_ACTION("VELO", "FORCE", force, "TIME", time, "TRACK", track, "FADE", fade, "RAGDOLL", ragdoll, "RELATIVE FROM BRANCH", relative_to_origin); }
    #macro state(state, value, time, no_burst, cancel_on_end) { !INTERNAL_ACTION("STATE", "STATE", state, "VALUE", value, "TIME", time, "DISABLE BURST", no_burst, "CANCEL ON END", cancel_on_end); }
    #macro hitbox(offset, rotation, size, damage, stun_time, can_kill, blockable, alldir_block, multi_hit, hit_ragdoll, hit_self, cancel_target, destruction, type, stun_anim) { !INTERNAL_ACTION("HITBOX", "ATTACK TYPE", type, "DAMAGE", damage, "STUN", stun_time, "STUN ANIM", stun_anim, "POSITION", offset, "ROTATION", rotation, "SIZE", size); }
    #macro anim(fade_out, fade_in, speed, looped, preview, anim_use) { !INTERNAL_ACTION("ANIM", "FADE OUT", fade_out, "FADE IN", fade_in, "SPEED", speed, "LOOPED", looped, "PREVIEW", preview, "ANIM_USE", anim_use); }
    #macro sfx(id, start, end, global_sfx, speed, vol, proj_tag, cancel) { !INTERNAL_ACTION("SFX", "ID", id, "START", start, "END", end, "GLOBAL", global_sfx, "SPEED", speed, "VOLUME", vol, "PROJECTILE TAG", proj_tag, "CANCEL", cancel); }
    #macro visual(effect, time, part, pos, rot, size, amount, vis_tag) { !INTERNAL_ACTION("VISUAL", "EFFECT", effect, "TIME", time, "BODY PART", part, "POSITION", pos, "ROTATION", rot, "SIZE", size, "AMOUNT", amount, "VISUAL TAG", vis_tag); }
    #macro grab(time, pos, rot) { !INTERNAL_ACTION("GRAB", "TIME", time, "POSITION", pos, "ROTATION", rot); }
    #macro projectile(type, damage, speed, time, stun, pos, rot, size, continue_proj, proj_tag) { !INTERNAL_ACTION("PROJECTILE", "ATTACK TYPE", type, "DAMAGE", damage, "SPEED", speed, "TIME", time, "STUN", stun, "POSITION", pos, "ROTATION", rot, "SIZE", size, "CONTINUE", continue_proj, "PROJECTILE TAG", proj_tag); }
    #macro counter(time, stun, attack_types, cancel_enemy) { !INTERNAL_ACTION("COUNTER", "TIME", time, "STUN", stun, "ATTACK TYPE2", attack_types, "CANCEL ENEMY", cancel_enemy); }
    #macro ultgib(amt) { !INTERNAL_ACTION("ULTGIB", "AMOUNT", amt); }
    #macro hpgib(amt) { !INTERNAL_ACTION("HPGIB", "AMOUNT", amt); }
    #macro evgib(amt) { !INTERNAL_ACTION("EVGIB", "AMOUNT", amt); }
    #macro loop(amt, loop_back, hold) { !INTERNAL_ACTION("LOOP", "LOOP AMOUNT", amt, "LOOP BACK", loop_back, "HOLD", hold); }
    """

    def __init__(self):
        self.moves = []
        self.vars = {}
        self.consts = {}
        self.macros = {}
        self.prefix = "!SKLL_VAR-"
        self.current_move = None
        self.current_logic = None
        self.scope_stack = []
        self.key_counter = 1

        self.registered_specials = {
            "hit": self._build_spec_hit
        }

    def _split_args(self, s):
        args = []
        current = []
        depth = 0
        in_quote = False
        for char in s:
            if char == '"':
                in_quote = not in_quote
            elif char == '[' and not in_quote:
                depth += 1
            elif char == ']' and not in_quote:
                depth -= 1
            elif char == ',' and not in_quote and depth == 0:
                args.append(''.join(current).strip())
                current = []
                continue
            current.append(char)
        if current:
            args.append(''.join(current).strip())
        return args

    def _build_spec_hit(self, raw_args, branch_name):
        flip = raw_args[0] if len(raw_args) > 0 else "false"
        if flip.lower() == 'true': flip = True
        elif flip.lower() == 'false': flip = False
        
        endlag = raw_args[1] if len(raw_args) > 1 else "0"
        endlag = float(endlag) if '.' in endlag else int(endlag)
        
        return {"K_NAME": "HITCNCL", "TIME": 1, "FLIP": flip, "ENDLAG": endlag, "BRANCH": branch_name}

    def _build_spec_target(self, raw_args, branch_name):
        return {"K_NAME": "BRANCH", "LAST HIT": 1, "BRANCH": branch_name}

    def emit(self, data):
        json_str = json.dumps(data).encode('utf-8')
        cctx = zstd.ZstdCompressor(level=3)
        compressed = cctx.compress(json_str)
        return base64.b64encode(compressed).decode('utf-8')

    def pre_process(self, lines):
        clean_lines = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            macro_m = re.match(r'#macro\s+([a-zA-Z_]\w*)\s*\(([^)]*)\)\s*\{(.*)', line)

            if macro_m:
                name = macro_m.group(1)
                args = [a for a in self._split_args(macro_m.group(2)) if a]
                rest_of_line = macro_m.group(3).strip()

                body = []
                if rest_of_line.endswith('}'):
                    content = rest_of_line[:-1].strip()
                    if content: body.append(content)
                    self.macros[name] = {"args": args, "body": body}
                else:
                    if rest_of_line: body.append(rest_of_line)
                    i += 1
                    while i < len(lines) and lines[i].strip() != "}":
                        body.append(lines[i].strip())
                        i += 1
                    self.macros[name] = {"args": args, "body": body}
            else:
                clean_lines.append(line)
            i += 1

        final_lines = []
        i = 0
        while i < len(clean_lines):
            line = clean_lines[i].strip()
            unroll_m = re.match(r'#unroll\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*\{', line)
            
            if unroll_m:
                iterations = int(unroll_m.group(1))
                start_idx = int(unroll_m.group(2))
                
                loop_body = []
                i += 1
                bracket_depth = 1
                while i < len(clean_lines):
                    l_line = clean_lines[i].strip()
                    if '{' in l_line: bracket_depth += l_line.count('{')
                    if '}' in l_line: bracket_depth -= l_line.count('}')
                    
                    if bracket_depth == 0:
                        break
                    loop_body.append(clean_lines[i])
                    i += 1
                
                for step in range(start_idx, start_idx + iterations):
                    for b_line in loop_body:
                        substituted = b_line
                        substituted = substituted.replace("$i", str(step))
                        math_matches = re.findall(r'\$\(([^)]+)\)', substituted)
                        for expr in math_matches:
                            safe_expr = expr.replace("i", str(step))
                            try:
                                evaluated = str(eval(safe_expr, {"__builtins__": None}))
                                substituted = substituted.replace(f"$({expr})", evaluated)
                            except Exception:
                                pass
                        final_lines.append(substituted)
            else:
                final_lines.append(clean_lines[i])
            i += 1
                
        return final_lines

    def expand_macros(self, lines):
        expanded = []
        for line in lines:
            call_m = re.match(r'([a-zA-Z_]\w*)\s*\(([^)]*)\)\s*;?', line)
            if call_m and call_m.group(1) in self.macros:
                m_name = call_m.group(1)
                m_data = self.macros[m_name]
                passed_args = [a for a in self._split_args(call_m.group(2)) if a]

                for b_line in m_data["body"]:
                    new_line = b_line
                    for placeholder, value in zip(m_data["args"], passed_args):
                        new_line = new_line.replace(placeholder, value)
                    expanded.append(new_line)
            else:
                expanded.append(line)
        return expanded

    def compile_file(self, file_path):
        raw_lines = []
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                raw_lines = f.readlines()

        all_lines = self.STDLIB.splitlines() + raw_lines
        lines = self.pre_process(all_lines)
        lines = self.expand_macros(lines)

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('//'): continue

            internal_m = re.match(r'!INTERNAL_ACTION\((.*)\)\s*;?', line)
            if internal_m and self.current_logic is not None:
                raw_args = self._split_args(internal_m.group(1))
                raw_args = [a.strip('"') for a in raw_args if a]

                if len(raw_args) >= 1:
                    entry = {"K_NAME": raw_args[0]}
                    for i in range(1, len(raw_args), 2):
                        if i + 1 < len(raw_args):
                            key = raw_args[i]
                            val = raw_args[i+1]
                            
                            try:
                                if val.startswith('[') and val.endswith(']'):
                                    val = json.loads(val)
                                elif val.lower() == 'true': val = True
                                elif val.lower() == 'false': val = False
                                else: val = float(val) if '.' in val else int(val)
                            except (ValueError, json.JSONDecodeError):
                                pass
                            
                            entry[key] = val

                    self.current_logic.append(entry)
                continue

            const_m = re.match(r'const\s+([a-zA-Z_]\w*)\s*=\s*([^;]+)\s*;?', line)
            if const_m:
                val = const_m.group(2).strip()
                if val.lower() == 'true': val = '1'
                elif val.lower() == 'false': val = '0'
                self.consts[const_m.group(1)] = val
                continue

            let_m = re.match(r'let\s+([a-zA-Z_]\w*)\s*=\s*([^;]+)\s*;?', line)
            if let_m:
                val = let_m.group(2).strip()
                if val.lower() == 'true': val = '1'
                elif val.lower() == 'false': val = '0'
                self.vars[let_m.group(1)] = val
                continue

            skill_m = re.match(r'skill\s+"([^"]+)"\s*\{', line)
            if skill_m:
                self.current_move = {
                    "NAME": skill_m.group(1),
                    "K_NAME": "SKILL",
                    "ADD": False,
                    "KEY": self.key_counter,
                    "DATA": {"Line": [], "Prop": {}, "Req": [], "Branch": {}},
                    "COOLDOWN": 0
                }
                self.current_logic = self.current_move["DATA"]["Line"]
                self.scope_stack = []
                self.key_counter += 1
                continue

            if line.startswith('cooldown:'):
                val = re.search(r'[\d.]+', line).group()
                self.current_move["COOLDOWN"] = float(val)
                continue

            spec_m = re.match(r'#special\s+([a-zA-Z_]\w*)\s*\(([^)]*)\)\s*\{', line)
            if spec_m:
                spec_name = spec_m.group(1).lower()

                if spec_name not in self.registered_specials:
                    print(f"Compile Error (Line {line_num}): special name '{spec_name}' not valid.")
                    sys.exit(1)

                raw_args = [a.strip('"') for a in self._split_args(spec_m.group(2)) if a]
                branch_name = f"B_SPEC_{spec_name.upper()}_{line_num}"
                self.current_move["DATA"]["Branch"][branch_name] = {"Line": []}

                node = self.registered_specials[spec_name](raw_args, branch_name)

                self.current_logic.append(node)

                self.scope_stack.append({
                    "type": "special",
                    "prev_logic": self.current_logic,
                    "loop_node": None
                })

                self.current_logic = self.current_move["DATA"]["Branch"][branch_name]["Line"]
                continue

            flow_m = re.match(r'(if|while)\s*\(\s*([^ ]+)\s*([<>=!]+)\s*([^ ]+)\s*\)\s*\{', line)
            if flow_m:
                flow_type, lhs, op, rhs = flow_m.groups()
                if rhs.lower() == 'true': rhs = '1'
                elif rhs.lower() == 'false': rhs = '0'

                final_var = f"{self.prefix}{lhs}" if lhs in self.vars else lhs
                if lhs not in self.vars and rhs in self.vars:
                    flip = {"<":">", ">":"<", "==":"=="}
                    lhs, rhs, op = rhs, lhs, flip.get(op, op)

                val_str = f"{op[0] if op in ['<','>'] else ''}{rhs}"
                branch_name = f"B_{flow_type}_{lhs}_{op}_{rhs}_{line_num}"

                self.current_move["DATA"]["Branch"][branch_name] = {"Line": []}
                
                tag_node = {
                    "K_NAME": "TAG",
                    "CHECK": True,
                    "TAG": final_var,
                    "VALUE": val_str,
                    "BRANCH": branch_name
                }
                
                self.current_logic.append(tag_node)

                self.scope_stack.append({
                    "type": flow_type,
                    "prev_logic": self.current_logic,
                    "loop_node": tag_node.copy() if flow_type == "while" else None
                })

                self.current_logic = self.current_move["DATA"]["Branch"][branch_name]["Line"]
                continue

            inc_dec = re.match(r'([a-zA-Z_]\w*)(\+\+|\-\-)\s*;?', line)
            if inc_dec:
                var, op = inc_dec.groups()
                self.current_logic.append({
                    "K_NAME": "TAG", "TAG": f"{self.prefix}{var}",
                    "SET": False, "ADD": (op == "++"), "VALUE": "1",
                    "TIME": 10000000000000000, "BRANCH": "nil"
                })
                continue

            if line.strip() == '}':
                if self.scope_stack:
                    scope = self.scope_stack.pop()
                    
                    if scope["type"] == "while":
                        self.current_logic.append(scope["loop_node"])
                        
                    self.current_logic = scope["prev_logic"]
                elif self.current_move:
                    self.current_move["DATA"] = json.dumps(self.current_move["DATA"])
                    self.moves.append(self.current_move)
                    self.current_move = None

        if self.vars:
            init = [{"K_NAME":"TAG", "TAG":f"{self.prefix}{k}", "SET":True, "VALUE":v, "TIME":10000000000000000, "BRANCH": "nil"} for k,v in self.vars.items()]
            self.moves.insert(0, {
                "NAME": "SKLL_INIT", "K_NAME": "SKILL", "COOLDOWN": 0, "ADD": False, "KEY": 1,
                "DATA": json.dumps({"Line": init, "Prop": {"USE":True, "INV":True, "AWK":True, "AWK2":True}})
            })

        return self.emit(self.moves)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python SKLL.py <filename.skll>")
    else:
        compiler = SKLL_Engine()
        result = compiler.compile_file(sys.argv[1])
        if result:
            print("compiled: ")
            print(result)
