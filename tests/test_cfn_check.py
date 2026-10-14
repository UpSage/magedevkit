"""The pipeline kit's CloudFormation template (pipeline/templates/deploy/infra/
stack.json) and its offline checker (bin/lib/cfn-check.py): the checker
catches broken templates, and the template keeps the kit's safety rules —
per-environment ECR names, a box document that runs nothing but the box
commands, an operator policy without IAM or a shell, no secret parameters."""

import copy
import json
import os
import re
import subprocess
import tempfile
import unittest

import helpers

LIB = os.path.join(helpers.REPO, "bin", "lib")
KIT = os.path.join(helpers.REPO, "pipeline", "templates", "deploy")
TEMPLATE = os.path.join(KIT, "infra", "stack.json")


def load():
    with open(TEMPLATE) as f:
        return json.load(f)


def statements(policy_doc):
    out = []
    for s in policy_doc["Statement"]:
        if isinstance(s, dict) and "Fn::If" in s:
            s = s["Fn::If"][1]
        out.append(s)
    return out


def actions(stmt):
    a = stmt["Action"]
    return [a] if isinstance(a, str) else a


class Checker(unittest.TestCase):
    def check(self, template, params=None):
        d = tempfile.mkdtemp(prefix="cfn-")
        t = os.path.join(d, "t.json")
        with open(t, "w") as f:
            json.dump(template, f)
        args = ["python3", os.path.join(LIB, "cfn-check.py"), t]
        if params is not None:
            p = os.path.join(d, "p.json")
            with open(p, "w") as f:
                json.dump(params, f)
            args.append(p)
        r = subprocess.run(args, capture_output=True, text=True)
        return r.returncode, r.stdout

    def test_the_kit_template_passes(self):
        self.assertEqual(self.check(load()), (0, ""))

    def test_broken_references_are_reported(self):
        cases = {
            "Ref": lambda t: t["Resources"]["PublicSubnet"]["Properties"].__setitem__("VpcId", {"Ref": "Nope"}),
            "GetAtt": lambda t: t["Resources"]["BuildProject"]["Properties"].__setitem__("ServiceRole", {"Fn::GetAtt": ["Nope", "Arn"]}),
            "Sub": lambda t: t["Resources"]["Vpc"]["Properties"]["Tags"][0].__setitem__("Value", {"Fn::Sub": "${Nope}-x"}),
            "DependsOn": lambda t: t["Resources"]["PublicRoute"].__setitem__("DependsOn", "Nope"),
            "Condition": lambda t: t["Resources"]["SchedulerRole"].__setitem__("Condition", "Nope"),
            "Type": lambda t: t["Resources"]["Vpc"].__setitem__("Type", "AWS::EC2::VPCC"),
            "secret": lambda t: t["Parameters"].__setitem__("DbPassword", {"Type": "String"}),
        }
        for name, breakit in cases.items():
            t = copy.deepcopy(load())
            breakit(t)
            code, out = self.check(t)
            self.assertEqual(code, 1, name)
            self.assertNotEqual(out, "", name)

    def test_sub_with_own_variables_and_pseudo_parameters(self):
        t = copy.deepcopy(load())
        t["Resources"]["Vpc"]["Properties"]["Tags"][0]["Value"] = {"Fn::Sub": ["${X}-${AWS::Region}-${App}", {"X": "y"}]}
        self.assertEqual(self.check(t), (0, ""))

    def test_params_file_must_match(self):
        t = load()
        required = sorted(k for k, v in t["Parameters"].items() if "Default" not in v)
        good = [{"ParameterKey": k, "ParameterValue": "x"} for k in required]
        code, out = self.check(t, good)
        self.assertEqual(code, 1)  # "x" doesn't match the patterns
        self.assertIn("doesn't match", out)
        code, out = self.check(t, good[1:] + [{"ParameterKey": "Bogus", "ParameterValue": "1"}])
        self.assertIn("doesn't set %s" % required[0], out)
        self.assertIn("sets Bogus", out)

    def test_oversize_is_reported(self):
        t = copy.deepcopy(load())
        t["Metadata"]["pad"] = "x" * 60000
        code, out = self.check(t)
        self.assertEqual(code, 1)
        self.assertIn("inline limit", out)


class KitTemplate(unittest.TestCase):
    def setUp(self):
        self.t = load()
        self.r = self.t["Resources"]

    def test_ecr_names_include_the_environment(self):
        for logical, svc in (("PhpRepository", "php"), ("NginxRepository", "nginx")):
            self.assertEqual(self.r[logical]["Properties"]["RepositoryName"], {"Fn::Sub": "${App}-${Env}-" + svc})
            self.assertIn('"countNumber": 20', self.r[logical]["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])

    def test_box_document_runs_only_box_commands(self):
        content = self.r["BoxDocument"]["Properties"]["Content"]
        allowed = content["parameters"]["command"]["allowedValues"]
        with open(os.path.join(KIT, "scripts", "box")) as f:
            box = f.read()
        handled = set(re.findall(r"^    ([a-z-]+)(?:\|[a-z-]+)*\)", box, re.M))
        self.assertEqual([a for a in allowed if a not in handled], [])
        for shell_only in ("compose", "magento", "seed-db", "rollback"):
            self.assertNotIn(shell_only, allowed)
        steps = content["mainSteps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["inputs"]["runCommand"],
                         [{"Fn::Sub": "sudo -iu deploy /opt/${App}/deploy/scripts/box {{command}} {{arg}}"}])
        pattern = content["parameters"]["arg"]["allowedPattern"]
        for bad in ("a;b", "$(id)", "a b", "`x`", "a|b", "a&b", "a\nb"):
            self.assertIsNone(re.fullmatch(pattern, bad), bad)

    def test_operator_policy_has_no_iam_shell_or_wildcards(self):
        stmts = statements(self.r["OperatorPolicy"]["Properties"]["PolicyDocument"])
        all_actions = [a for s in stmts for a in actions(s)]
        self.assertFalse([a for a in all_actions if a.startswith("iam:") or a == "*" or a.endswith(":*")])
        self.assertFalse([a for a in all_actions if a.startswith("ssm:StartSession")])
        send = [s for s in stmts if "ssm:SendCommand" in actions(s)]
        self.assertEqual(len(send), 1)
        self.assertNotIn("AWS-RunShellScript", json.dumps(send[0]["Resource"]))
        self.assertIn("document/${BoxDocument}", json.dumps(send[0]["Resource"]))

    def test_box_role_writes_only_its_status(self):
        stmts = statements(self.r["BoxRole"]["Properties"]["Policies"][0]["PolicyDocument"])
        writes = [s for s in stmts if "ssm:PutParameter" in actions(s)]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["Resource"],
                         {"Fn::Sub": "arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/${App}/${Env}/status"})

    def test_no_secrets_in_parameters_or_outputs(self):
        for name in list(self.t["Parameters"]) + list(self.t["Outputs"]):
            self.assertIsNone(re.search(r"(?i)password|secret|token|crypt", name), name)
        params = [r for r in self.r.values() if r["Type"] == "AWS::SSM::Parameter"]
        self.assertEqual([p["Properties"]["Type"] for p in params], ["String"])  # only the status, never a secret

    def test_box_keeps_imdsv2_and_an_encrypted_disk(self):
        lt = self.r["BoxLaunchTemplate"]["Properties"]["LaunchTemplateData"]["MetadataOptions"]
        self.assertEqual(lt["HttpTokens"], "required")
        ebs = self.r["Box"]["Properties"]["BlockDeviceMappings"][0]["Ebs"]
        self.assertTrue(ebs["Encrypted"])
        ingress = self.r["WebSecurityGroup"]["Properties"]["SecurityGroupIngress"]
        self.assertEqual(sorted(i["FromPort"] for i in ingress), [80, 443])  # no SSH


if __name__ == "__main__":
    unittest.main()
