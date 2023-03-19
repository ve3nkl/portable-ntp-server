length1 = 59.5;
width1  = 22.4;
height1 = 1.4;
length2 = length1 - 1.4*2;
width2  = width1  - 1.0*2;
height2 = 2.15;
thickness = 1.5;
round_r = 2.0;

ant_enc_length = 30.0;
ant_enc_width  = 28.0;
ant_enc_height = 13.0;
ant_enc_r = 2.0;
ant_enc_thickness = 1.5;

sm = 0.1;
$fn = 100;

module smoothed_cube(length, width, height, radius, smooth_type) {
    small = 0.1;
    if (smooth_type == "all") {
        translate([radius, radius, radius]) {
            minkowski() {
                cube([length-radius*2, width-radius*2, height-radius*2]);
                sphere(radius);
            };
        };
    } else {
        if (smooth_type == "height") {
            translate([radius, radius, 0]) {
                minkowski() {
                    cube([length-radius*2, width-radius*2, height-small]);
                    cylinder(r=radius, h=small);
                };
            };            
        };
        if (smooth_type == "length") {
            translate([0, radius, radius]) {
                minkowski() {
                    cube([length-small, width-radius*2, height-radius*2]);
                    rotate([0, 90, 0]) {
                        cylinder(r=radius, h=small);
                    };
                };
            };            
        };
        if (smooth_type == "width") {
            translate([radius, small, radius]) {
                minkowski() {
                    cube([length-radius*2, width-small, height-radius*2]);
                    rotate([90, 0, 0]) {
                        cylinder(r=radius, h=small);
                    };
                };
            };            
        };
    };
};

module side_cover() {
    difference() {
        union() {
            smoothed_cube(length1, width1, height1, round_r, "height");
            translate([(length1-length2)/2, (width1-width2)/2, height1-sm]) 
                smoothed_cube(length2, width2, height2+sm, round_r, "height");
        };
        translate([(length1-length2)/2+thickness, (width1-width2)/2+thickness, -sm])
            smoothed_cube(length2-thickness*2, width2-thickness*2, height1+height2-thickness+sm, round_r, "height");
    };
};

difference() {
    union() {
        side_cover();
        translate([(length1-ant_enc_length)/2.0, (width1-ant_enc_height)/2.0, height1+height2-sm])
            smoothed_cube(ant_enc_length, ant_enc_height, ant_enc_width+sm, ant_enc_r, "height");
    };
    translate([(length1-ant_enc_length)/2.0+ant_enc_thickness,(width1-ant_enc_height)/2.0+ant_enc_thickness,-sm])
        smoothed_cube(ant_enc_length-ant_enc_thickness*2, ant_enc_height-ant_enc_thickness*2, ant_enc_width-ant_enc_thickness+height1+height2+sm, ant_enc_r, "height");
};
