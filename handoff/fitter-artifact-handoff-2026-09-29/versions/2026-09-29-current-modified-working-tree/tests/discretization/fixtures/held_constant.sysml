package HeldConstant {
    private import ScalarValues::*;
    metadata def Neural;
    metadata def NeuralRequirement;
    metadata def Prohibition;
    metadata def Completion;
    metadata def ContinuousRate;
    part def Controller {
        attribute physical : Real := 1.0;
        attribute sampled : Real := 0.0;
        attribute active : Boolean := false;
        attribute irrelevantClock : Real := 0.0;
        #Neural action def Policy {
            in reading : Real;
            #Completion in done : Boolean;
            out on : Boolean;
        }
        #NeuralRequirement requirement def 'Controller rule' {
            subject p : Policy;
            require constraint { (p.reading < 0.0) == p.on }
        }
        action step {
            in dt : Real;
            #ContinuousRate assign physical := physical + 0.0 * dt;
            assign irrelevantClock := irrelevantClock + dt;
            assign sampled := physical;
            action policyCall : Policy {
                in reading := sampled;
                in done := false;
            }
            assign active := policyCall.on;
        }
    }
    part def System {
        part controller : Controller;
        #Prohibition requirement def 'Respond to negative reading' {
            subject s : System;
            require constraint { s.controller.sampled < 0.0 implies s.controller.active }
        }
    }
    part system : System;
}
